"""Regression: a token issued by an OAuth gateway survives a router refresh.

The bug this locks down: `OAuthProxy` stores the `jti → upstream token` mapping
in `client_storage`. Building that store per install meant every `refresh()`
(which reinstalls the gateway, and which the router runs on any unknown path)
left every token already handed out pointing at a mapping that no longer
existed. The token still verified by signature, then read back as
unauthenticated — a 401 moments after a successful login, with nothing in the
log beyond "jti mapping not found".

The test drives the provider directly rather than through Google: what is under
test is the store's lifetime, not the identity provider.
"""

import pytest

from gateway.proxy.providers import build_auth_provider


@pytest.fixture()
def oauth_config() -> dict:
    return {
        "id": "gw_regression01",
        "slug": "demo",
        "auth_mode": "oauth",
        "idp": "google",
        "required_scopes": ["openid", "email"],
    }


CREDENTIALS = {
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "client-secret-value",
    "jwt_signing_key": "k" * 64,
}


def _issue(config: dict) -> tuple[str, object]:
    """Build the provider the way the router does, and mint a token."""
    provider = build_auth_provider(
        config, base_url="https://gw.example.dev", credentials=dict(CREDENTIALS)
    )
    assert provider is not None
    provider.set_mcp_path("/mcp")
    token = provider._jwt_issuer.issue_access_token(
        client_id="https://client.example.dev/metadata.json",
        scopes=["openid"],
        jti="jti-under-test",
    )
    return token, provider


def test_issued_token_survives_a_reinstall(oauth_config: dict) -> None:
    """A rebuild of the same gateway must not orphan tokens already issued."""
    token, first = _issue(oauth_config)

    # What `Router.refresh()` does on every config reload and on unknown paths.
    second = build_auth_provider(
        oauth_config, base_url="https://gw.example.dev", credentials=dict(CREDENTIALS)
    )
    assert second is not None
    second.set_mcp_path("/mcp")

    # The signature checks out either way. What must survive is the state the jti
    # points into: the adapter objects are per-provider by design, but they read
    # the same backing store, so a mapping written by one is visible to the other.
    assert second._jwt_issuer.verify_token(token)["jti"] == "jti-under-test"
    assert first._client_storage is second._client_storage, (
        "the backing store was rebuilt, so tokens issued before the refresh dangle"
    )


def test_a_reminted_token_resolves_after_a_reinstall(oauth_config: dict) -> None:
    """End to end: write a mapping through one install, read it through the next.

    This is the assertion the user actually experienced — the signature is
    valid, so the only visible symptom is `verify_token` answering None.
    """
    import asyncio

    token, first = _issue(oauth_config)
    jti = first._jwt_issuer.verify_token(token)["jti"]

    async def write_mapping() -> None:
        from fastmcp.server.auth.oauth_proxy.models import JTIMapping

        await first._jti_mapping_store.put(
            key=jti,
            value=JTIMapping(jti=jti, upstream_token_id="tok_1",
                             created_at=int(__import__("time").time())),
            ttl=600,
        )

    asyncio.run(write_mapping())

    second = build_auth_provider(
        oauth_config, base_url="https://gw.example.dev", credentials=dict(CREDENTIALS)
    )
    assert second is not None
    second.set_mcp_path("/mcp")

    async def read_mapping() -> object:
        return await second._jti_mapping_store.get(key=jti)

    found = asyncio.run(read_mapping())
    assert found is not None, "mapping written before the refresh was lost"
    assert found.upstream_token_id == "tok_1"


def test_a_deleted_gateway_releases_its_state(oauth_config: dict) -> None:
    """Dropping a gateway must not leak its store for the process lifetime."""
    from gateway.proxy.providers import forget_client_store

    _token, provider = _issue(oauth_config)
    store = provider._client_storage

    forget_client_store(oauth_config["id"])

    _rebuilt_token, rebuilt = _issue(oauth_config)
    assert rebuilt._client_storage is not store
