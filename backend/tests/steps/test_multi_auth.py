"""Steps for gateways that accept more than one kind of credential.

Two shapes are covered, and they fail differently:

* a **key beside a login** — one route, two ways to prove who you are. The
  failure mode is that the guard picks one path and silently drops the other,
  so every scenario here makes a real call through the live ASGI app.
* **several issuers** — the client already holds a token from its own
  authorization server; the gateway only verifies it. The failure mode is the
  opposite (a token is accepted that should not be), so the negative case
  matters as much as the positive one.

The JWT scenarios use the same `FakeGoogle` the OAuth tests use: a real JWKS
endpoint and a real signature, because "we validate JWTs" is exactly the claim
a hand-written verifier gets wrong.
"""

import time

import httpx
import pytest
from joserfc import jwt
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.domain.events import TENANT_OF_DEPLOYMENT, AuthMode, IdentityProvider, VerifierSpec
from gateway.domain.model import DomainError
from tests.steps.fake_google import FakeGoogle
from tests.steps.support import BackendServer

scenarios("../features/multi_auth.feature")

VERIFIER_CLIENT = "multi-auth-verifier.apps.googleusercontent.com"
VERIFIER_SECRET = "multi-auth-verifier-secret"

@pytest.fixture()
def ctx() -> dict:
    return {}

@pytest.fixture()
def backend(runner) -> BackendServer:
    server = BackendServer()
    runner.run(server.start())
    try:
        yield server
    finally:
        runner.run(server.stop())

@pytest.fixture()
def second_issuer(runner) -> FakeGoogle:
    """A second authorization server, on its own port, that mints real JWTs."""
    provider = FakeGoogle(client_id=VERIFIER_CLIENT, client_secret=VERIFIER_SECRET)
    runner.run(provider.start())
    try:
        yield provider
    finally:
        runner.run(provider.stop())

# -- given -----------------------------------------------------------------

def _create(container, runner, operator: str, *, slug: str, target: str,
            auth_mode: AuthMode = AuthMode.NONE) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url=target, actor=operator,
            auth_mode=auth_mode,
            idp=IdentityProvider.GITHUB if auth_mode is AuthMode.OAUTH else None,
        )
        if auth_mode is AuthMode.OAUTH:
            await container.commands.configure_auth(
                gateway_id,
                auth_mode=AuthMode.OAUTH,
                idp=IdentityProvider.GITHUB,
                client_id="Ov23liMultiAuthClientId",
                client_secret="multi-auth-client-secret-value-32",
                actor=operator,
            )
        await container.refresh_router()
        return await container.read_models.get_gateway(
            gateway_id, TENANT_OF_DEPLOYMENT
        )

    return runner.run(create())

def _configure(container, runner, operator: str, gateway: dict, **kwargs) -> dict:
    """Re-save the whole auth block, which is what `PUT /auth` declares."""
    async def configure() -> dict:
        await container.commands.configure_auth(
            gateway["id"],
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
            **kwargs,
            actor=operator,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(
            gateway["id"], TENANT_OF_DEPLOYMENT
        )

    return runner.run(configure())

@given(
    parsers.parse('a gateway "{slug}" with oauth github auth proxying a real MCP server'),
    target_fixture="gateway",
)
def oauth_gateway_real(container, runner, operator: str, backend: BackendServer,
                       slug: str) -> dict:
    return _create(container, runner, operator, slug=slug, target=backend.url,
                   auth_mode=AuthMode.OAUTH)

@given(parsers.parse('a gateway "{slug}" with oauth github auth'),
       target_fixture="gateway")
def oauth_gateway(container, runner, operator: str, slug: str) -> dict:
    return _create(container, runner, operator, slug=slug,
                   target="http://127.0.0.1:1/mcp", auth_mode=AuthMode.OAUTH)

@given(parsers.parse('a gateway "{slug}" with no auth'), target_fixture="gateway")
def public_gateway(container, runner, operator: str, slug: str) -> dict:
    return _create(container, runner, operator, slug=slug,
                   target="http://127.0.0.1:1/mcp")

@pytest.fixture()
def issued_key(container, runner, operator: str, gateway: dict) -> dict:
    async def issue() -> dict:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway["id"], label="multi", actor=operator
        )
        await container.refresh_router()
        return {"plaintext": plaintext, "key_id": key_id}

    return runner.run(issue())

@given("an API key was issued for the gateway")
def issued_key_step(issued_key: dict) -> dict:
    return issued_key

@given(parsers.parse('the account "{email}" belongs to the gateway\'s tenant'))
def account_in_gateway_tenant(container, runner, gateway: dict, email: str) -> None:
    """The gate is tenancy now, so the account has to exist in that tenant.

    Registered through the command handler: the read model row is what the guard
    looks up, and where it came from does not change the answer.
    """

    async def ensure() -> None:
        if await container.store.fetch_operator_by_email(email):
            return
        await container.commands.register_operator(
            email=email, password="correct-horse-battery",
            tenant_id=gateway["tenant_id"],
        )

    runner.run(ensure())

@given("the gateway also accepts API keys")
def also_accepts_keys(container, runner, operator: str, gateway: dict,
                      issued_key: dict) -> None:
    """Re-save auth with keys on. `PUT /auth` declares the whole block, so the
    gateway's existing extra issuers have to be carried over by hand."""
    _configure(container, runner, operator, gateway, allow_api_key=True,
               verifiers=tuple(
                   VerifierSpec(**verifier) for verifier in gateway.get("verifiers") or []
               ))

@given(
    "a gateway \"demo\" accepting tokens from a second authorization server",
    target_fixture="gateway",
)
def gateway_with_second_issuer(container, runner, operator: str,
                               backend: BackendServer,
                               second_issuer: FakeGoogle) -> dict:
    gateway = _create(container, runner, operator, slug="demo", target=backend.url,
                      auth_mode=AuthMode.OAUTH)
    return _configure(
        container, runner, operator, gateway,
        verifiers=(_verifier(second_issuer),),
    )

def _verifier(idp: FakeGoogle, label: str = "corp") -> VerifierSpec:
    return VerifierSpec(
        label=label,
        issuer=idp.issuer,
        audience=idp.client_id,
        jwks_uri=f"{idp.issuer}/oauth2/v4/jwks",
    )

# -- when ------------------------------------------------------------------

@when("a caller with the issued key lists the tools", target_fixture="probe")
def list_tools(live_server: str, gateway: dict, issued_key: dict) -> dict:
    return _tools_list(live_server, gateway["slug"], issued_key["plaintext"])

@when("an anonymous caller probes the gateway", target_fixture="probe")
def anonymous_probe(live_server: str, gateway: dict) -> dict:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json=_initialize_payload(),
        headers={"Accept": "application/json, text/event-stream"},
        timeout=10,
        follow_redirects=False,
    )
    return {"status": response.status_code, "body": response.text,
            "headers": dict(response.headers)}

@when("the operator revokes every key", target_fixture="revoked_key")
def revoke_every_key(container, runner, operator: str, gateway: dict,
                     issued_key: dict) -> dict:
    runner.run(
        container.commands.revoke_api_key(
            gateway["id"], key_id=issued_key["key_id"], actor=operator
        )
    )
    runner.run(container.refresh_router())
    return issued_key

@when("the operator rotates that key", target_fixture="rotated_key")
def rotate_key(container, runner, operator: str, gateway: dict,
               issued_key: dict) -> dict:
    async def rotate() -> dict:
        plaintext, key_id = await container.commands.rotate_api_key(
            gateway["id"], key_id=issued_key["key_id"], actor=operator
        )
        await container.refresh_router()
        return {"plaintext": plaintext, "key_id": key_id}

    return runner.run(rotate())

@then("the rotated key lists the tools")
def rotated_key_lists(live_server: str, gateway: dict, rotated_key: dict) -> None:
    probe = _tools_list(live_server, gateway["slug"], rotated_key["plaintext"])
    assert probe["status"] == 200, probe["body"][:300]
    assert "echo" in probe["body"], probe["body"][:300]

@when(parsers.parse('the identity "{subject}" is resolved against the gateway'),
      target_fixture="resolved")
def resolve_identity(container, runner, gateway: dict, subject: str) -> dict:
    """Ask the live guard, with `resolve` stubbed to the identity under test.

    The allowlist is the real one; only the provider's signature check is
    bypassed, because it needs a real IdP. Same approach as the proxy suite.
    """
    async def check() -> dict:
        from gateway.proxy.identity import Identity

        live = container.router.live_gateways[gateway["slug"]]
        endpoint = next(
            route.endpoint for route in live.routes
            if hasattr(route.endpoint, "_authorized")
        )
        identity = Identity(subject=subject, source="github")
        return {"subject": subject,
                "allowed": bool(await endpoint._authorized(identity.subject))}

    return runner.run(check())

@then("the identity is refused")
def identity_refused(resolved: dict) -> None:
    assert resolved["allowed"] is False, f"{resolved['subject']} was admitted"

@when("a caller presents a token issued by that authorization server",
      target_fixture="probe")
def token_from_second_issuer(live_server: str, gateway: dict,
                             second_issuer: FakeGoogle) -> dict:
    return _probe_with(live_server, gateway, _mint(second_issuer))

@when("a caller presents a token from an unrelated issuer", target_fixture="probe")
def token_from_unrelated_issuer(live_server: str, gateway: dict,
                                second_issuer: FakeGoogle) -> dict:
    """Signed by the same key, claiming another issuer: signature alone is not
    identity, and a verifier that forgot `iss` would admit this."""
    return _probe_with(live_server, gateway, _mint(second_issuer, issuer="https://evil.example"))

MEMBER_SUBJECT = "member@example.dev"

def _mint(idp: FakeGoogle, *, issuer: str | None = None,
          subject: str = MEMBER_SUBJECT) -> str:
    claims = {
        "iss": issuer or idp.issuer,
        "aud": idp.client_id,
        "sub": subject,
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    return jwt.encode({"alg": "RS256", "kid": idp.key.kid}, claims, idp.key)

def _probe_with(live_server: str, gateway: dict, token: str) -> dict:
    """A token from an extra issuer must be admitted by the transport too.

    `call_mcp` returns the *initialize* response, which carries the status a
    refusal would use; the tools list is what proves the call reached upstream.
    """
    return _tools_list(live_server, gateway["slug"], token)

def _tools_list(live_server: str, slug: str, token: str) -> dict:
    """Full handshake, then `tools/list`: a 200 alone proves nothing about auth."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    with httpx.Client(timeout=20) as client:
        started = client.post(
            f"{live_server}/g/{slug}/mcp", json=_initialize_payload(), headers=headers
        )
        if started.status_code != 200:
            return {"status": started.status_code, "body": started.text}
        session = started.headers.get("mcp-session-id", "")
        headers = {**headers, "mcp-session-id": session}
        client.post(
            f"{live_server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        listed = client.post(
            f"{live_server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        return {"status": listed.status_code, "body": listed.text}

@when(parsers.parse('the operator adds two verifiers with the label "{label}"'))
def two_verifiers_same_label(container, runner, operator: str, gateway: dict,
                             second_issuer: FakeGoogle, label: str,
                             ctx: dict) -> None:
    ctx["error"] = _refusal(
        container, runner, operator, gateway,
        verifiers=(_verifier(second_issuer, label), _verifier(second_issuer, label)),
    )

@when("the operator enables API keys on it")
def enable_api_keys(container, runner, operator: str, gateway: dict,
                    ctx: dict) -> None:
    ctx["error"] = _refusal(container, runner, operator, gateway,
                            allow_api_key=True)

def _refusal(container, runner, operator: str, gateway: dict, **kwargs) -> str:
    """Re-save auth on the gateway's *current* mode; return the refusal, if any."""
    mode = AuthMode(gateway["auth_mode"])
    idp = IdentityProvider(gateway["idp"]) if gateway.get("idp") else None

    async def configure() -> str:
        try:
            await container.commands.configure_auth(
                gateway["id"],
                auth_mode=mode,
                idp=idp,
                client_id=gateway.get("client_id") or None,
                config_url=gateway.get("config_url") or None,
                # A stored secret is kept when omitted, which is what the panel
                # sends when the operator did not retype it.
                **kwargs,
                actor=operator,
            )
        except DomainError as exc:
            return str(exc)
        return ""

    return runner.run(configure())

# -- then ------------------------------------------------------------------

@then(parsers.parse('the backend tool "{tool}" is visible'))
def tool_visible(probe: dict, tool: str) -> None:
    assert probe["status"] == 200, probe["body"][:300]
    assert tool in probe["body"], probe["body"][:300]

@then(parsers.parse("the caller receives {status:d}"))
def receives_status(probe: dict, status: int) -> None:
    assert probe["status"] == status, f"got {probe['status']}: {probe['body'][:200]}"

@then("the challenge names a resource metadata URL")
def challenge_names_metadata(probe: dict) -> None:
    challenge = probe["headers"].get("www-authenticate", "")
    assert "resource_metadata" in challenge, challenge

@then("the call is admitted")
def call_admitted(probe: dict) -> None:
    assert probe["status"] == 200, probe["body"][:300]

@then("the revoked key is refused with 401")
def revoked_key_refused(live_server: str, gateway: dict, revoked_key: dict) -> None:
    probe = _tools_list(live_server, gateway["slug"], revoked_key["plaintext"])
    assert probe["status"] == 401, f"{probe['status']}: {probe['body'][:200]}"

@then(parsers.parse('configuring it fails with "{reason}"'))
def configuring_fails(ctx: dict, reason: str) -> None:
    assert reason in ctx.get("error", ""), ctx.get("error") or "it succeeded"

# -- per-source allowlist ---------------------------------------------------

@when(parsers.parse('the operator allows only the tool "{tool}"'))
def allow_one_tool(container, runner, operator: str, gateway: dict, tool: str) -> None:
    async def save() -> None:
        await container.commands.set_gateway_tools(
            gateway["id"], tools=[tool], actor=operator
        )
        await container.refresh_router()

    runner.run(save())

@when(parsers.parse('the operator allows only the tool "{tool}" for the source "{source}"'))
def allow_one_tool_for_source(container, runner, operator: str, gateway: dict,
                              tool: str, source: str, ctx: dict) -> None:
    """Set one source's list; a gateway-wide refusal lands in `ctx["error"]`."""
    async def save() -> str:
        try:
            await container.commands.set_gateway_tools(
                gateway["id"], tools=[tool], source=source, actor=operator
            )
        except DomainError as exc:
            return str(exc)
        await container.refresh_router()
        return ""

    ctx["error"] = runner.run(save())

@when(parsers.parse('the operator clears the list for the source "{source}"'))
def clear_source_list(container, runner, operator: str, gateway: dict,
                      source: str) -> None:
    async def save() -> None:
        await container.commands.set_gateway_tools(
            gateway["id"], tools=[], source=source, actor=operator
        )
        await container.refresh_router()

    runner.run(save())

@then(parsers.parse('the listed tools are exactly "{tool}"'))
def listed_tools_exactly(probe: dict, tool: str) -> None:
    assert probe["status"] == 200, probe["body"][:300]
    assert _listed_names(probe["body"]) == [tool], probe["body"][:400]

@then(parsers.parse('the listed tools include "{tool}"'))
def listed_tools_include(probe: dict, tool: str) -> None:
    """The other source is untouched: its catalog is the gateway's, not the key's."""
    assert probe["status"] == 200, probe["body"][:300]
    assert tool in _listed_names(probe["body"]), probe["body"][:400]

@then(parsers.parse('a caller with the issued key sees only "{tool}"'))
def key_sees_only(live_server: str, gateway: dict, issued_key: dict,
                  tool: str) -> None:
    probe = _tools_list(live_server, gateway["slug"], issued_key["plaintext"])
    assert _listed_names(probe["body"]) == [tool], probe["body"][:400]

@then(parsers.parse('calling "{tool}" with the issued key is refused with 403'))
def blocked_for_source(live_server: str, gateway: dict, issued_key: dict,
                       tool: str) -> None:
    result = _call_tool(live_server, gateway["slug"], issued_key["plaintext"], tool)
    assert result["status"] == 403, f"{result['status']}: {result['body'][:300]}"
    assert tool in result["body"]

@then(parsers.parse('setting the allowlist fails with "{reason}"'))
def allowlist_fails(ctx: dict, reason: str) -> None:
    assert reason in ctx.get("error", ""), ctx.get("error") or "it succeeded"

# -- helpers ---------------------------------------------------------------

def _listed_names(body: str) -> list[str]:
    """Tool names out of a `tools/list` answer, JSON or SSE framed."""
    import json

    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = json.loads(line[len("data:"):].strip())
        result = payload.get("result") or {}
        if "tools" in result:
            return [tool["name"] for tool in result["tools"]]
    payload = json.loads(body)
    return [tool["name"] for tool in payload["result"]["tools"]]

def _call_tool(live_server: str, slug: str, token: str, tool: str) -> dict:
    """Handshake, then one `tools/call`; the call's own status is what matters."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    with httpx.Client(timeout=20) as client:
        started = client.post(
            f"{live_server}/g/{slug}/mcp", json=_initialize_payload(), headers=headers
        )
        if started.status_code != 200:
            return {"status": started.status_code, "body": started.text}
        headers = {**headers, "mcp-session-id": started.headers.get("mcp-session-id", "")}
        client.post(
            f"{live_server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        called = client.post(
            f"{live_server}/g/{slug}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"repo": "x", "text": "x"}},
            },
            headers=headers,
        )
        return {"status": called.status_code, "body": called.text}

def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
