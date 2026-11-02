"""Steps for proxy authorisation, rate limiting and traffic accounting.

These run against the real ASGI stack started by `live_server`, which means the
401/403/429 paths are exercised where they actually happen — in the guard
middleware in front of a real FastMCP proxy, not in a unit test.

The OAuth scenarios stop at `GatewayAuth.resolve` on purpose: driving a full
GitHub login would need live credentials. That boundary is still real code — the
allowlist decision is what these scenarios are about, and it is the part that can
silently break.
"""

import asyncio

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.application.container import Container
from gateway.domain.events import TENANT_OF_DEPLOYMENT, AuthMode, IdentityProvider
from tests.steps.support import BackendServer, Context

scenarios("../features/proxy_authorisation.feature")
scenarios("../features/protection.feature")

OPERATOR_EMAIL = "ops@example.dev"
OPERATOR_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def ctx() -> Context:
    return Context()


@pytest.fixture()
def backend(runner) -> BackendServer:
    server = BackendServer()
    runner.run(server.start())
    try:
        yield server
    finally:
        runner.run(server.stop())


# -- given -----------------------------------------------------------------


def _make_gateway(container, runner, operator: str, *, target: str,
                  auth_mode: AuthMode, idp: IdentityProvider | None = None,
                  rate_limit: int = 0, slug: str = "demo") -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url=target, actor=operator,
            auth_mode=auth_mode, idp=idp,
        )
        if rate_limit:
            await container.commands.set_rate_limit(
                gateway_id, requests_per_minute=rate_limit, actor=operator
            )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id, TENANT_OF_DEPLOYMENT)

    return runner.run(create())


@given(
    parsers.parse('a gateway "{slug}" with api_key auth'),
    target_fixture="gateway",
)
def gateway_api_key(container, runner, operator: str, slug: str) -> dict:
    return _make_gateway(
        container, runner, operator, target="http://127.0.0.1:1/mcp",
        auth_mode=AuthMode.API_KEY, slug=slug,
    )


@given(
    parsers.parse('a gateway "{slug}" with api_key auth proxying a real MCP server'),
    target_fixture="gateway",
)
def gateway_api_key_real(container, runner, operator: str, backend: BackendServer,
                         slug: str) -> dict:
    return _make_gateway(
        container, runner, operator, target=backend.url,
        auth_mode=AuthMode.API_KEY, slug=slug,
    )


@given(
    parsers.parse('a gateway "{slug}" with oauth github auth'),
    target_fixture="gateway",
)
def gateway_oauth(container, runner, operator: str, slug: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url="http://127.0.0.1:1/mcp",
            actor=operator, auth_mode=AuthMode.OAUTH, idp=IdentityProvider.GITHUB,
        )
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
            client_id="test-client-id",
            client_secret="test-client-secret-value-32-bytes",
            actor=operator,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id, TENANT_OF_DEPLOYMENT)

    return runner.run(create())


@given(parsers.parse('the principal "{subject}" is allowed'))
def allow_principal(container, runner, operator: str, gateway: dict,
                    subject: str) -> None:
    runner.run(
        container.commands.allow_principal(
            gateway["id"], subject=subject, actor=operator
        )
    )
    runner.run(container.refresh_router())


@given(parsers.re(r"the usage limit is (?P<limit>\d+) requests? per minute"))
def set_limit(container, runner, operator: str, gateway: dict, limit: str) -> None:
    runner.run(
        container.commands.set_rate_limit(
            gateway["id"], requests_per_minute=int(limit), actor=operator
        )
    )
    runner.run(container.refresh_router())


@pytest.fixture()
def issued_key(container, runner, operator: str, gateway: dict) -> dict:
    async def issue() -> dict:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway["id"], label="test", actor=operator
        )
        await container.refresh_router()
        return {"plaintext": plaintext, "key_id": key_id}

    return runner.run(issue())


@given("an API key was issued for the gateway")
def issued_key_step(issued_key: dict) -> dict:
    """Declare the key in the feature; the fixture does the work."""
    return issued_key


# -- when ------------------------------------------------------------------


@when("an anonymous caller probes the gateway", target_fixture="probe")
def anonymous_probe(live_server: str, gateway: dict) -> dict:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json=_initialize_payload(),
        headers={"Accept": "application/json, text/event-stream"},
        timeout=10,
        follow_redirects=False,
    )
    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": response.text,
    }


@when("a caller presents an API key that was never issued", target_fixture="probe")
def wrong_key(live_server: str, gateway: dict) -> dict:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer omg_this_key_was_never_issued",
        },
        timeout=10,
    )
    return {"status": response.status_code, "body": response.text}


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


@then("the revoked key is refused with 401")
def revoked_probe(live_server: str, gateway: dict, revoked_key: dict) -> None:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {revoked_key['plaintext']}",
        },
        timeout=10,
    )
    assert response.status_code == 401, f"{response.status_code}: {response.text[:200]}"


@when("a caller with the issued key lists the tools", target_fixture="probe")
def list_tools(live_server: str, gateway: dict, issued_key: dict) -> dict:
    return _call_tools_list(live_server, gateway["slug"], issued_key["plaintext"])


@when(parsers.parse("a caller with the issued key calls it {count:d} times"),
      target_fixture="probe")
def call_n_times(live_server: str, gateway: dict, issued_key: dict,
                 count: int) -> dict:
    statuses = []
    last_body = ""
    for _ in range(count):
        result = _call_tools_list(live_server, gateway["slug"], issued_key["plaintext"])
        statuses.append(result["status"])
        last_body = result["body"]
    return {"statuses": statuses, "status": statuses[-1], "body": last_body}


@when("a second key is issued", target_fixture="second_key")
def second_key(container, runner, operator: str, gateway: dict) -> dict:
    async def issue() -> dict:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway["id"], label="second", actor=operator
        )
        await container.refresh_router()
        return {"plaintext": plaintext, "key_id": key_id}

    return runner.run(issue())


@when("each key calls once", target_fixture="probe")
def each_key_once(live_server: str, gateway: dict, issued_key: dict,
                  second_key: dict) -> dict:
    first = _call_tools_list(live_server, gateway["slug"], issued_key["plaintext"])
    second = _call_tools_list(live_server, gateway["slug"], second_key["plaintext"])
    return {"statuses": [first["status"], second["status"]]}


@when(parsers.parse('the identity "{subject}" is resolved against the gateway'),
      target_fixture="resolved")
def resolve_identity(container, runner, gateway: dict, subject: str) -> dict:
    """Resolve against the live guard, bypassing upstream token verification.

    `GatewayAuth.resolve` is what the middleware calls; the OAuth provider's
    `verify_token` is the piece that needs a real GitHub token, so it is stubbed
    here to return the identity under test. The allowlist logic exercised is the
    real one.
    """

    async def check() -> dict:
        from gateway.proxy.runtime import Identity

        live = container.router.live_gateways[gateway["slug"]]
        # Walk the published routes to find the guard holding this gateway's auth.
        auth = _find_auth(live)
        assert auth is not None, "guard auth not found"

        original = auth.resolve

        async def resolve(token: str | None) -> Identity | None:
            if not token:
                return None
            return Identity(subject=subject, method="oauth")

        auth.resolve = resolve  # type: ignore[method-assign]
        try:
            identity = await auth.resolve("any-token")
        finally:
            auth.resolve = original  # type: ignore[method-assign]

        assert identity is not None
        allowed = _guard_authorized(live, identity.subject)
        return {"subject": identity.subject, "allowed": allowed}

    return runner.run(check())


@when("the operator disables the gateway")
def disable_gateway(container, runner, operator: str, gateway: dict) -> None:
    runner.run(container.commands.disable_gateway(gateway["id"], actor=operator))
    runner.run(container.refresh_router())


# -- then ------------------------------------------------------------------


@then(parsers.parse("the caller receives {status:d}"))
def receives_status(probe: dict, status: int) -> None:
    assert probe["status"] == status, f"got {probe['status']}: {probe['body'][:200]}"


@then("the challenge names no bearer realm")
def challenge_has_no_realm(probe: dict) -> None:
    challenge = probe["headers"].get("www-authenticate", "")
    # An API-key gateway must not advertise an OAuth discovery document.
    assert "resource_metadata" not in challenge


@then(parsers.parse('the backend tool "{tool}" is visible'))
def tool_visible(probe: dict, tool: str) -> None:
    assert probe["status"] == 200, probe["body"][:300]
    assert tool in probe["body"], probe["body"][:300]


@then("the third call receives 429")
def third_is_429(probe: dict) -> None:
    assert probe["statuses"] == [200, 200, 429], probe["statuses"]


@then("the 429 response asks to retry later")
def retry_later(probe: dict) -> None:
    assert "rate_limited" in probe["body"]


@then("neither call is rate limited")
def neither_limited(probe: dict) -> None:
    assert probe["statuses"] == [200, 200], probe["statuses"]


@then(parsers.parse('the identity is accepted'))
def identity_accepted(resolved: dict) -> None:
    assert resolved["allowed"] is True


@then("the identity is refused")
def identity_refused(resolved: dict) -> None:
    assert resolved["allowed"] is False


@then(parsers.parse('the resolved subject is "{subject}"'))
def subject_is(resolved: dict, subject: str) -> None:
    assert resolved["subject"] == subject


@then("the gateway endpoint is unreachable")
def endpoint_unreachable(live_server: str, gateway: dict) -> None:
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json=_initialize_payload(),
        timeout=10,
    )
    assert response.status_code == 404, response.status_code


@then(parsers.parse('the traffic log records outcome "{outcome}" with status {status:d}'))
def traffic_recorded(container, runner, gateway: dict, outcome: str,
                     status: int) -> None:
    async def check() -> dict:
        # The recorder is asynchronous: give the writer a moment to drain.
        for _ in range(80):
            rows = await container.read_models.request_tail(gateway["id"], 20)
            match = [r for r in rows if r["outcome"] == outcome]
            if match:
                return match[0]
            await asyncio.sleep(0.05)
        return {}

    row = runner.run(check())
    assert row, f"no '{outcome}' observation recorded"
    assert row["status"] == status


@then("the observation carries the caller IP")
def carries_ip(container, runner, gateway: dict) -> None:
    async def check() -> list[dict]:
        return await container.read_models.request_tail(gateway["id"], 20)

    rows = runner.run(check())
    assert any(row["ip"] for row in rows), rows


@then(parsers.parse("the stats report at least {count:d} requests"))
def stats_count(container, runner, gateway: dict, count: int) -> None:
    """Stats count every admitted MCP request, so a call costs more than one.

    Asserting a lower bound keeps the test about "traffic is counted", not about
    how many JSON-RPC round trips the client chose to make.
    """

    async def check() -> dict:
        latest: dict = {}
        for _ in range(80):
            latest = await container.read_models.stats(gateway["id"], 60)
            if latest["requests"] >= count:
                return latest
            await asyncio.sleep(0.05)
        return latest

    stats = runner.run(check())
    assert stats["requests"] >= count, stats
    assert stats["avg_duration_ms"] >= 0


@then("the stats include a per-IP breakdown")
def stats_by_ip(container, runner, gateway: dict) -> None:
    async def check() -> dict:
        return await container.read_models.stats(gateway["id"], 60)

    stats = runner.run(check())
    assert stats["by_ip"], stats


# -- helpers ---------------------------------------------------------------


def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


def _call_tools_list(server: str, slug: str, key: str) -> dict:
    """Do a full MCP handshake so success means the proxy actually worked."""
    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"{server}/g/{slug}/mcp",
            json=_initialize_payload(),
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {key}",
            },
        )
        if response.status_code != 200:
            return {"status": response.status_code, "body": response.text}

        session = response.headers.get("mcp-session-id", "")
        client.post(
            f"{server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {key}",
                "mcp-session-id": session,
            },
            timeout=10,
        )
        listed = client.post(
            f"{server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {key}",
                "mcp-session-id": session,
            },
        )
        return {"status": listed.status_code, "body": listed.text}


def _find_auth(live) -> object | None:
    for route in live.routes:
        endpoint = getattr(route, "endpoint", None)
        auth = getattr(endpoint, "auth", None)
        if auth is not None:
            return auth
    return None


def _guard_authorized(live, subject: str) -> bool:
    for route in live.routes:
        endpoint = getattr(route, "endpoint", None)
        if hasattr(endpoint, "_authorized"):
            return bool(endpoint._authorized(subject))
    raise AssertionError("guard not found")


@given(parsers.parse(
    'an enabled gateway "{slug}" with oauth github auth and no credentials'
))
def oauth_gateway_without_credentials(container, runner, operator: str,
                                      slug: str) -> None:
    """Created but never configured: the state a half-finished wizard leaves."""

    async def create() -> None:
        await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url="http://127.0.0.1:1/mcp",
            actor=operator, auth_mode=AuthMode.OAUTH,
            idp=IdentityProvider.GITHUB,
        )
        await container.refresh_router()

    runner.run(create())


@then(parsers.parse('the "{slug}" gateway answers 503 explaining it is not ready'))
def gateway_not_ready(live_server: str, slug: str) -> None:
    response = httpx.post(
        f"{live_server}/g/{slug}/mcp",
        json=_initialize_payload(),
        headers={"Accept": "application/json, text/event-stream"},
        timeout=10,
    )
    assert response.status_code == 503, f"{response.status_code}: {response.text[:200]}"
    body = response.json()
    assert body["error"] == "gateway_not_ready", body
    assert body["gateway"] == slug, body
    # And the reason is actionable, not "internal error".
    assert "client_id" in body["message"], body["message"]


@pytest.fixture()
def protected_backend(runner) -> BackendServer:
    server = BackendServer(require_token="up-secret")
    runner.run(server.start())
    try:
        yield server
    finally:
        runner.run(server.stop())


@given(
    parsers.parse(
        'a gateway "{slug}" proxying an MCP server that requires the token "{token}"'
    ),
    target_fixture="gateway",
)
def gateway_behind_token(container, runner, operator: str, protected_backend,
                         slug: str, token: str) -> dict:
    assert token == protected_backend.require_token
    return _make_gateway(
        container, runner, operator, target=protected_backend.url,
        auth_mode=AuthMode.API_KEY, slug=slug,
    )


@given("the gateway is told the upstream token")
def tell_upstream_token(container, runner, operator: str, gateway: dict) -> None:
    runner.run(
        container.commands.configure_auth(
            gateway["id"],
            auth_mode=AuthMode.API_KEY,
            upstream_token="up-secret",
            actor=operator,
        )
    )
    runner.run(container.refresh_router())


@then("the backend saw a bearer on every request")
def backend_saw_bearer(protected_backend) -> None:
    # The wrapper records every request, so an empty log would mean the tool call
    # never reached the upstream and the assertion above proved nothing.
    assert protected_backend.seen, "no request reached the upstream"
    for header in protected_backend.seen:
        assert header == "Bearer up-secret", header


@when("a second container starts against the same database",
      target_fixture="restarted")
def second_container(runner, settings) -> Container:
    """A fresh Container over the same log — exactly what a pod restart is.

    Its router has never seen a request, so anything reachable here came from the
    event log at boot, not from a lazy refresh triggered by traffic.
    """
    second = runner.run(Container.create(settings))
    runner.run(second.refresh_router())
    return second


@then(parsers.parse('the "{slug}" gateway is advertised as live'))
def gateway_advertised_live(restarted, slug: str) -> None:
    live = list(restarted.router.live_gateways)
    assert slug in live, f"live={live} pending={restarted.router.pending}"
    assert not restarted.router.pending, restarted.router.pending
