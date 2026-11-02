"""Steps for OAuth discovery.

This is the scenario that makes "log in with GitHub" real rather than a promise:
a standard MCP client reads the 401 challenge, fetches the protected-resource
metadata, then the authorization-server metadata, registers itself, and starts a
browser flow. If any URL in that chain 404s, no client can ever authenticate —
which is precisely the bug that motivated these tests (metadata served under the
mount instead of at the root).

A fake `client_id`/`client_secret` is enough: discovery is generated locally and
GitHub's endpoints are hardcoded in the provider, so no network call is made.
"""

from urllib.parse import urlparse

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.domain.events import AuthMode, IdentityProvider

scenarios("../features/oauth_discovery.feature")


@pytest.fixture()
def gateway(container, runner, operator: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            slug="demo",
            name="Demo",
            target_url="http://127.0.0.1:1/mcp",
            actor=operator,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
        )
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
            client_id="Ov23liTestClientId",
            client_secret="test-client-secret-value-32-bytes",
            actor=operator,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id)

    return runner.run(create())


@pytest.fixture()
def api_key_gateway(container, runner, operator: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            slug="demo",
            name="Demo",
            target_url="http://127.0.0.1:1/mcp",
            actor=operator,
            auth_mode=AuthMode.API_KEY,
        )
        await container.commands.issue_api_key(gateway_id, label="t", actor=operator)
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id)

    return runner.run(create())


# -- given -----------------------------------------------------------------


@given(
    parsers.parse('a gateway "{slug}" with oauth github auth'),
    target_fixture="gateway",
)
def oauth_gateway(container, runner, operator: str, slug: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            slug=slug,
            name=slug.title(),
            target_url="http://127.0.0.1:1/mcp",
            actor=operator,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
        )
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
            client_id="Ov23liTestClientId",
            client_secret="test-client-secret-value-32-bytes",
            actor=operator,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id)

    return runner.run(create())


@given(
    parsers.parse('a gateway "{slug}" with api_key auth'),
    target_fixture="gateway",
)
def api_key_auth_gateway(container, runner, operator: str, slug: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            slug=slug,
            name=slug.title(),
            target_url="http://127.0.0.1:1/mcp",
            actor=operator,
            auth_mode=AuthMode.API_KEY,
        )
        await container.commands.issue_api_key(gateway_id, label="t", actor=operator)
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id)

    return runner.run(create())


# -- when ------------------------------------------------------------------


@when("an anonymous caller probes the gateway", target_fixture="probe")
def anonymous_probe(live_server: str, gateway: dict) -> dict:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
        timeout=10,
    )
    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": response.text,
    }


@when("the client follows the discovery chain", target_fixture="discovery")
def follow_discovery(live_server: str, gateway: dict, probe: dict) -> dict:
    """Walk the URLs a real MCP client walks, from the 401 challenge onward."""
    challenge = probe["headers"].get("www-authenticate", "")
    assert "resource_metadata=" in challenge, challenge
    advertised = challenge.split('resource_metadata="', 1)[1].rstrip('"')

    # The gateway advertises its *public* base URL (`https://gateway.test` in
    # tests); the suite only serves it over plain HTTP on a random port, so keep
    # the path and retarget the origin. Paths are what this test is about.
    here = urlparse(live_server)
    metadata_url = here._replace(path=urlparse(advertised).path).geturl()

    with httpx.Client(timeout=10) as client:
        resource = client.get(metadata_url)
        result: dict = {
            "advertised_path": urlparse(advertised).path,
            "resource_url": metadata_url,
            "resource_path": urlparse(metadata_url).path,
            "resource_status": resource.status_code,
        }
        if resource.status_code != 200:
            return result
        resource_doc = resource.json()
        result["resource"] = resource_doc

        issuer = resource_doc["authorization_servers"][0]
        # RFC 8414 path-insertion form: the issuer path goes after the suffix.
        as_path = f"/.well-known/oauth-authorization-server{urlparse(issuer).path}"
        as_metadata = client.get(here._replace(path=as_path).geturl())
        result["as_path"] = as_path
        result["as_status"] = as_metadata.status_code
        result["as"] = as_metadata.json() if as_metadata.status_code == 200 else {}

        # And the mount-prefixed variant must NOT be how clients are told to go.
        prefixed = client.get(
            here._replace(
                path=f"{urlparse(issuer).path}/.well-known/oauth-authorization-server"
            ).geturl()
        )
        result["prefixed_status"] = prefixed.status_code
    return result


# -- then ------------------------------------------------------------------


@then(parsers.parse("the caller receives {status:d}"))
def receives_status(probe: dict, status: int) -> None:
    assert probe["status"] == status, f"{probe['status']}: {probe['body'][:200]}"


@then("the challenge names a resource metadata URL")
def challenge_names_metadata(probe: dict) -> None:
    challenge = probe["headers"].get("www-authenticate", "")
    assert "resource_metadata=" in challenge, challenge
    assert "/.well-known/oauth-protected-resource/g/" in challenge, challenge


@then("the challenge names no resource metadata URL")
def challenge_has_no_metadata(probe: dict) -> None:
    challenge = probe["headers"].get("www-authenticate", "")
    assert "resource_metadata=" not in challenge, challenge


@then("the protected resource metadata names the gateway endpoint")
def resource_names_endpoint(discovery: dict, gateway: dict) -> None:
    assert discovery["resource_status"] == 200, discovery.get("resource_path")
    assert discovery["resource"]["resource"].endswith(
        f"/g/{gateway['slug']}/mcp"
    ), discovery["resource"]


@then("the authorization server metadata names the per-gateway issuer")
def as_names_issuer(discovery: dict, gateway: dict) -> None:
    assert discovery["as_status"] == 200, discovery.get("as_path")
    assert discovery["as"]["issuer"].endswith(
        f"/g/{gateway['slug']}"
    ), discovery["as"]["issuer"]
    # Endpoints must be absolute and point at the per-gateway prefix.
    assert discovery["as"]["authorization_endpoint"].endswith(
        f"/g/{gateway['slug']}/authorize"
    ), discovery["as"]["authorization_endpoint"]


@then("the authorization server metadata advertises a registration endpoint")
def as_names_registration(discovery: dict) -> None:
    # Dynamic Client Registration is what lets a client like OpenCode connect
    # without an operator pre-registering it.
    assert discovery["as"]["registration_endpoint"].endswith("/register")


@then("the protected resource document is served from the root path")
def served_at_root(discovery: dict) -> None:
    assert discovery["resource_path"].startswith(
        "/.well-known/oauth-protected-resource/g/"
    ), discovery["resource_path"]
    assert discovery["resource_status"] == 200


@then("it is not served beneath the gateway prefix")
def not_served_under_prefix(discovery: dict) -> None:
    # A document that only exists under the mount is unreachable for a client
    # that follows the challenge, because the advertised URL is root-relative.
    assert not discovery["resource_path"].startswith("/g/"), discovery["resource_path"]


@then("the authorization server document is not served")
def as_not_served(live_server: str) -> None:
    response = httpx.get(
        f"{live_server}/.well-known/oauth-authorization-server/g/demo", timeout=10
    )
    assert response.status_code == 404, response.status_code


@given(
    parsers.parse(
        'a gateway "demo" with oauth github auth requiring the scope "{scope}"'
    ),
    target_fixture="gateway",
)
def oauth_gateway_with_scope(container, runner, operator: str, scope: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            slug="demo", name="Demo", target_url="http://127.0.0.1:1/mcp",
            actor=operator, auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
        )
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
            client_id="Ov23liTestClientId",
            client_secret="test-client-secret-value-32-bytes",
            required_scopes=(scope,),
            actor=operator,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id)

    return runner.run(create())


@then(parsers.parse(
    'the protected resource metadata advertises exactly the scope "{scope}"'
))
def resource_advertises_scope(discovery: dict, scope: str) -> None:
    assert discovery["resource_status"] == 200, discovery.get("resource_path")
    assert discovery["resource"]["scopes_supported"] == [scope], discovery["resource"]


@when("a client registers itself and starts the authorisation flow",
      target_fixture="flow")
def start_authorisation(live_server: str, gateway: dict) -> dict:
    """Walk the two endpoints the metadata advertises as if a real MCP client.

    Registration then `/authorize`. Both must answer; a 404 here means the
    metadata is advertising routes the router never installed.
    """
    base = f"{live_server}/g/{gateway['slug']}"
    client = httpx.Client()
    registered = client.post(
        f"{base}/register",
        json={
            "client_name": "probe",
            "redirect_uris": ["http://127.0.0.1:9999/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert registered.status_code in (200, 201), registered.text[:300]
    client_id = registered.json()["client_id"]

    # PKCE, as every MCP client uses.
    import hashlib
    from base64 import urlsafe_b64encode
    verifier = "v" * 64
    challenge = urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")

    started = client.get(
        f"{base}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:9999/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    return {
        "registered": registered, "started": started,
        "client_id": client_id, "origin": live_server, "client": client,
    }


@then("the authorisation endpoint answers 302")
def authorise_redirects(flow: dict) -> None:
    assert flow["started"].status_code == 302, (
        f"{flow['started'].status_code}: {flow['started'].text[:300]}"
    )


@then("the redirect goes to the identity provider")
def redirect_to_idp(flow: dict) -> None:
    """Follow the login the way a browser would, ending at the provider.

    The advertised URLs carry the public HTTPS base (as they must, or clients
    would be sent to a host that does not exist); the test server lives
    elsewhere, so keep the path and swap the origin. A cookie jar is required:
    the consent step refuses a submission that did not start the same session.
    """
    import re

    client = flow["client"]
    location = flow["started"].headers.get("location", "")
    assert location, flow["started"].headers
    path = "/" + location.split("/", 3)[3] if "://" in location else location

    consent = client.get(flow["origin"] + path, follow_redirects=False)
    assert consent.status_code == 200, f"{path} -> {consent.status_code}"

    # FastMCP asks for consent before handing off; approving is the step a user
    # does in a browser, so the test does it too.
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', consent.text))
    assert fields, "consent page has no form fields: " + consent.text[-400:]
    # The consent cookie is `Secure`, so an http test client drops it from the
    # jar. Forward it by hand: the server compares values, not transport.
    cookies = {}
    for header in consent.headers.get_list("set-cookie"):
        name, _, rest = header.partition("=")
        cookies[name.strip()] = rest.split(";", 1)[0]
    approved = client.post(
        flow["origin"] + path,
        data={**fields, "action": "approve"},
        cookies=cookies,
        follow_redirects=False,
    )
    target = approved.headers.get("location", "")
    assert approved.status_code == 302, f"{approved.status_code}: {approved.text[:300]}"
    assert "github.com/login/oauth/authorize" in target, target


@then("the registration endpoint answered with a client id")
def registration_returned_client(flow: dict) -> None:
    assert flow["client_id"], flow["registered"].text[:200]


@then(parsers.parse("health reports {count:d} gateways"))
def health_reports_gateways(count: int, container, runner) -> None:
    async def check() -> None:
        # Refresh first: the read model changed after the gateways were created.
        await container.refresh_router()
        assert len(container.router.live) == count, (
            f"live={container.router.live} pending={container.router.pending}"
        )

    runner.run(check())
