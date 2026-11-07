"""Steps for reason, per-tool usage, retention, key expiry, health, CSV and plan.

Two kinds of scenario live here and they are tested differently on purpose:

* the proxy ones (reason, tool usage, key expiry, health) run against the real
  ASGI stack and a real FastMCP backend, because the thing under test is the
  middleware in front of the proxy;
* the read-side ones (retention, CSV, plan) call the services directly or over
  HTTP with a session cookie, because there is no proxy hop in them.

The `operator` fixture from `conftest` owns the deployment tenant, which is where
proxy scenarios live; API scenarios register their own owner so a session cookie
can be carried.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import BackendServer, SecureCookieClient, owner_account

scenarios("../features/operations.feature")

PASSWORD = "correct-horse-battery"
OWNER = "ops-http@example.dev"


# --------------------------------------------------------------------- fixtures


@pytest.fixture()
def backend(runner) -> BackendServer:
    server = BackendServer()
    runner.run(server.start())
    try:
        yield server
    finally:
        runner.run(server.stop())


@pytest.fixture()
def owner(live_server: str) -> dict[str, Any]:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield owner_account(client, OWNER)
    finally:
        client.close()


@pytest.fixture()
def issued() -> dict[str, str]:
    return {}


# ------------------------------------------------------------------- gateways


async def _gateway(container, operator: str, slug: str, target: str) -> dict:
    from gateway.domain.events import AuthMode

    gateway_id = await container.commands.create_gateway(
        tenant_id=await _tenant(container),
        slug=slug, name=slug, target_url=target, actor=operator,
        auth_mode=AuthMode.API_KEY,
    )
    if target.startswith("http://backend.test"):
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.API_KEY,
            idp=None,
            client_id=None,
            client_secret=None,
            config_url=None,
            required_scopes=[],
            upstream_token=None,
            actor=operator,
        )
    await container.refresh_router()
    return await container.read_models.get_gateway(gateway_id, await _tenant(container))


async def _tenant(container) -> str:
    from gateway.domain.events import TENANT_OF_DEPLOYMENT

    return TENANT_OF_DEPLOYMENT


def _create(owner: dict, slug: str, target: str) -> dict:
    """One gateway, owned by the session account, reachable through the proxy.

    Created over HTTP rather than through the command handler: the read-side
    scenarios (keys, health, CSV) need it to belong to the same tenant as the
    session cookie, and a command-handler gateway lands in the deployment's
    default tenant instead. The proxy serves either — it only reads the row.
    """
    browser = owner["browser"]
    response = browser.post(
        "/api/gateways",
        json={"slug": slug, "name": slug, "target_url": target,
              "auth_mode": "api_key"},
    )
    assert response.status_code == 201, response.text
    created = browser.get(f"/api/gateways/{response.json()['id']}")
    assert created.status_code == 200, created.text
    return created.json()


@given(
    parsers.parse('a gateway "{slug}" with api_key auth proxying a real MCP server'),
    target_fixture="gateway",
)
def live_gateway(owner: dict, backend: BackendServer, slug: str) -> dict:
    return _create(owner, slug, backend.url)


@given(
    parsers.parse('a gateway "{slug}" pointing at "{target}"'),
    target_fixture="gateway",
)
def plain_gateway(owner: dict, slug: str, target: str) -> dict:
    return _create(owner, slug, target)


@given(parsers.parse('the account "{email}" belongs to another tenant'))
def account_in_another_tenant(container, runner, email: str) -> None:
    """A real account, in a tenant that does not own the gateway.

    Built with the command handler because an account can belong to exactly one
    tenant and the panel's only exits from onboarding are create and join. The
    read model is written by the same projection either way — what the gate
    consults is a row, not a list.
    """

    async def ensure() -> None:
        tenant_id = "tn_elsewhere"
        await container.commands.ensure_tenant(tenant_id=tenant_id, name="Elsewhere")
        await container.commands.register_operator(
            email=email, password="correct-horse-battery", tenant_id=tenant_id
        )

    runner.run(ensure())


@given(parsers.parse('the allowlist exposes only "{name}"'))
def only_tool(owner: dict, gateway: dict, name: str) -> None:
    response = owner["browser"].put(
        f"/api/gateways/{gateway['id']}/tools", json={"tools": [name]}
    )
    assert response.status_code == 200, response.text


# ----------------------------------------------------------------------- keys


@given("an API key was issued for the gateway", target_fixture="issued")
def issue_key(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/keys", json={"label": "test"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"plaintext": body["api_key"], "key_id": body["key_id"]}


@given("an API key was issued for the gateway that expired yesterday",
       target_fixture="issued")
def issue_expired_key(owner: dict, container, runner, gateway: dict) -> dict:
    """Issue a normal key, then move its deadline into the past.

    The domain refuses to mint a key that is already dead — that is a caller
    mistake, not a revocation — so "time passed" is simulated where time actually
    decides: the `expires_at` column the auth lookup compares against.
    """
    issued = issue_key(owner, gateway)

    async def age() -> None:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from gateway.infrastructure.db.tables import rm_api_keys

        async with container.database.session() as session, session.begin():
            await session.execute(
                update(rm_api_keys)
                .where(rm_api_keys.c.key_id == issued["key_id"])
                .values(expires_at=datetime.now(UTC) - timedelta(days=1))
            )

    runner.run(age())
    return issued


# ---------------------------------------------------------------------- calls


_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def _call(live_server: str, slug: str, key: str | None,
          tool: str | None = None) -> int:
    """A full MCP exchange, so "200" means the proxy actually worked.

    MCP needs initialize, then `notifications/initialized`, then the call; a bare
    POST answers "Missing session ID" and would prove nothing about auth.
    """
    headers = {"Accept": "application/json, text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx.Client(timeout=20) as client:
        started = client.post(
            f"{live_server}/g/{slug}/mcp", json=_INITIALIZE, headers=headers
        )
        if started.status_code != 200:
            return started.status_code
        session = started.headers.get("mcp-session-id", "")
        client.post(
            f"{live_server}/g/{slug}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={**headers, "mcp-session-id": session},
        )
        payload = (
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {}},
            }
            if tool
            else {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        return client.post(
            f"{live_server}/g/{slug}/mcp",
            json=payload,
            headers={**headers, "mcp-session-id": session},
        ).status_code


@when("a caller with the issued key calls it once", target_fixture="probe")
def one_call(live_server: str, gateway: dict, issued: dict) -> dict:
    return {"status": _call(live_server, gateway["slug"], issued["plaintext"])}


@when(parsers.parse("a caller with the issued key calls it {count:d} times"),
      target_fixture="probe")
def n_calls(live_server: str, gateway: dict, issued: dict, count: int) -> dict:
    statuses = [_call(live_server, gateway["slug"], issued["plaintext"])
                for _ in range(count)]
    return {"statuses": statuses, "status": statuses[-1]}


@when(parsers.parse(
    'a caller with the issued key calls tool "{tool}" {count:d} times'
), target_fixture="probe")
def tool_calls(live_server: str, gateway: dict, issued: dict, tool: str,
               count: int) -> dict:
    statuses = [
        _call(live_server, gateway["slug"], issued["plaintext"], tool=tool)
        for _ in range(count)
    ]
    return {"statuses": statuses, "status": statuses[-1]}


@when("an anonymous caller probes the gateway", target_fixture="probe")
def anonymous(live_server: str, gateway: dict) -> dict:
    return {"status": _call(live_server, gateway["slug"], None)}


@when("an outsider calls the gateway", target_fixture="probe")
def outsider_call(container, live_server: str, gateway: dict) -> dict:
    """A caller the guard can identify but the allowlist does not name.

    An API key cannot produce this: a key's subject is minted per key and the
    tenancy gate does not apply to it. So the identity is supplied the way a real
    provider would supply one — `resolve` is stubbed only for the signature
    check, and the request still goes over HTTP so the guard records the row.
    """
    from gateway.proxy.identity import Identity

    live = container.router.live_gateways[gateway["slug"]]
    endpoint = next(
        route.endpoint for route in live.routes
        if hasattr(route.endpoint, "_authorized")
    )

    async def admit(_token: str | None) -> Identity:
        return Identity(subject="someone-else@example.dev", source="github")

    original = endpoint.auth.resolve
    endpoint.auth.resolve = admit  # type: ignore[method-assign]
    try:
        return {"status": _call(live_server, gateway["slug"], "any-token")}
    finally:
        endpoint.auth.resolve = original  # type: ignore[method-assign]


@then(parsers.parse("the gateway answers {status:d}"))
def gateway_status(probe: dict, status: int) -> None:
    assert probe["status"] == status, probe


# --------------------------------------------------------------------- reason


def _latest(container, runner, gateway: dict, *, outcome: str | None = None,
            timeout: float = 4.0) -> dict:
    async def wait() -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            rows = await container.read_models.request_tail(gateway["id"], 20)
            wanted = [r for r in rows if outcome is None or r["outcome"] == outcome]
            if wanted:
                return wanted[0]
            await asyncio.sleep(0.05)
        return {}

    return runner.run(wait())


@then(parsers.parse('the traffic log records reason "{reason}"'))
def reason_recorded(container, runner, gateway: dict, reason: str) -> None:
    row = _latest(container, runner, gateway, outcome="forbidden")
    assert row, "no forbidden observation recorded"
    assert row["reason"] == reason, row


@when(parsers.parse('a caller with the issued key calls tool "{tool}" once'),
      target_fixture="probe")
def one_tool_call(live_server: str, gateway: dict, issued: dict,
                 tool: str) -> dict:
    return {"status": _call(live_server, gateway["slug"], issued["plaintext"],
                            tool=tool)}


@then("the traffic log records no reason")
def no_reason(container, runner, gateway: dict) -> None:
    row = _latest(container, runner, gateway, outcome="allowed")
    assert row, "no allowed observation recorded"
    assert not row["reason"], row


# ---------------------------------------------------------------------- usage


async def _stats(container, gateway: dict, *, wait_for: int = 0,
                 tool: str | None = None) -> dict:
    """Poll until the counter under test has caught up.

    `requests` is not a good barrier: one MCP call is initialize + notification +
    the call itself, so it reaches two before two tool calls were recorded.
    """
    for _ in range(100):
        stats = await container.read_models.stats(gateway["id"], 60)
        if tool is None:
            if stats["requests"] >= wait_for:
                return stats
        elif stats["by_tool"].get(tool, 0) >= wait_for:
            return stats
        await asyncio.sleep(0.05)
    return await container.read_models.stats(gateway["id"], 60)


@then(parsers.parse('the stats report tool "{tool}" was called {count:d} times'))
def stats_tools(container, runner, gateway: dict, tool: str, count: int) -> None:
    stats = runner.run(_stats(container, gateway, wait_for=count, tool=tool))
    assert stats["by_tool"].get(tool) == count, stats


@then("the stats report no tool usage")
def stats_no_tools(container, runner, gateway: dict) -> None:
    stats = runner.run(_stats(container, gateway, wait_for=1))
    assert stats["by_tool"] == {}, stats


# ------------------------------------------------------------------ retention


def _insert_observation(container, runner, gateway: dict, *, days_ago: int) -> str:
    async def insert() -> str:
        from gateway.domain.events import RequestObserved

        event = RequestObserved(
            aggregate_id=gateway["id"],
            gateway_id=gateway["id"],
            subject="api-key:old",
            outcome="allowed",
            status=200,
            duration_ms=12,
            tool="echo",
            # `occurred_at` is stamped on construction; rewrite it to make the
            # row older than the window under test without waiting a month.
            occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
        await container.projections.apply(0, event)
        return event.event_id

    return runner.run(insert())


@given("an observation recorded 40 days ago", target_fixture="old_event_id")
def old_observation(container, runner, gateway: dict) -> str:
    # One old row and one recent one, so the assertion is about the horizon and
    # not about an empty table that happens to satisfy it.
    _insert_observation(container, runner, gateway, days_ago=0)
    return _insert_observation(container, runner, gateway, days_ago=40)


@when(parsers.parse("the retention window is {days:d} days"))
def retention_window(container, runner, days: int) -> None:
    runner.run(container.retention.prune(days))


@then("the old observation is pruned")
def old_pruned(container, runner, gateway: dict, old_event_id: str) -> None:
    async def check() -> list[dict]:
        return await container.read_models.request_tail(gateway["id"], 200)

    rows = runner.run(check())
    assert all(row["event_id"] != old_event_id for row in rows), rows


@then("the recent observation is kept")
def recent_kept(container, runner, gateway: dict) -> None:
    async def check() -> list[dict]:
        return await container.read_models.request_tail(gateway["id"], 200)

    rows = runner.run(check())
    assert rows, "the recent observation disappeared with the pruned one"


@then("the retention step prunes nothing")
def prunes_nothing(container, runner, gateway: dict) -> None:
    async def count_before() -> int:
        rows = await container.read_models.request_tail(gateway["id"], 500)
        return len(rows)

    before = runner.run(count_before())
    runner.run(container.retention.prune(0))
    after = runner.run(count_before())
    assert after == before, (before, after)


@then("the audit log still has the gateway's creation event")
def audit_intact(owner_events: list[dict]) -> None:
    assert any(
        event["event_type"] == "gateway.created" for event in owner_events
    ), owner_events


@pytest.fixture()
def owner_events(container, runner) -> list[dict]:
    async def fetch() -> list[dict]:
        return await container.store.recent_events(limit=200)

    return runner.run(fetch())


# ----------------------------------------------------------------- key expiry


@when("the operator lists the gateway's keys over HTTP", target_fixture="keys_page")
def list_keys(owner: dict, gateway: dict) -> list[dict]:
    response = owner["browser"].get(f"/api/gateways/{gateway['id']}/keys")
    assert response.status_code == 200, response.text
    return response.json()["keys"]


@then("every key row carries an expiry field")
def keys_have_expiry(keys_page: list[dict]) -> None:
    assert keys_page, keys_page
    assert all("expires_at" in row for row in keys_page), keys_page


# --------------------------------------------------------------------- health


@when("the operator tests the gateway's upstream", target_fixture="health")
def probe_upstream(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].post(f"/api/gateways/{gateway['id']}/test")
    assert response.status_code == 200, response.text
    return response.json()


@then("the test reports the upstream is down")
def down(health: dict) -> None:
    assert health["up"] is False, health


@then("the test names what went wrong")
def names_the_failure(health: dict) -> None:
    assert health["detail"], health


@then("the test reports the upstream is up")
def up(health: dict) -> None:
    assert health["up"] is True, health


@then("the test counts the tools the upstream serves")
def counts_tools(health: dict) -> None:
    assert health["tools"] >= 1, health


@then("the gateway detail reports the upstream is up")
def detail_health(owner: dict, gateway: dict) -> None:
    response = owner["browser"].get(f"/api/gateways/{gateway['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["health"]["up"] is True, response.json()["health"]


# ------------------------------------------------------------------------ CSV


@when("the owner exports the request log as CSV", target_fixture="csv_response")
def export_csv(owner: dict) -> httpx.Response:
    response = owner["browser"].get("/api/requests.csv")
    assert response.status_code == 200, response.text
    return response


@then("the CSV has a header row")
def csv_header(csv_response: httpx.Response) -> None:
    first = csv_response.text.splitlines()[0]
    assert "outcome" in first and "gateway_id" in first, first


@then("the CSV has a row per exported request")
def csv_rows(csv_response: httpx.Response) -> None:
    assert len(csv_response.text.splitlines()) >= 2, csv_response.text


@then("the CSV rows carry the outcome column")
def csv_outcome(csv_response: httpx.Response) -> None:
    assert "allowed" in csv_response.text, csv_response.text[:400]


@given(parsers.parse('another tenant\'s gateway "{slug}" has traffic'),
       target_fixture="other_gateway")
def other_tenant_traffic(live_server: str, slug: str) -> dict:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        owner_account(client, f"{slug}@example.dev", "Other Co")
        created = client.post(
            "/api/gateways",
            json={"slug": slug, "name": slug, "target_url": "http://127.0.0.1:9/mcp"},
        )
        assert created.status_code == 201, created.text
        gateway = {**created.json(), "slug": slug}
        return gateway
    finally:
        client.close()


@then(parsers.parse('the CSV does not mention gateway "{slug}"'))
def csv_scoped(csv_response: httpx.Response, other_gateway: dict, slug: str) -> None:
    assert other_gateway["id"] not in csv_response.text, csv_response.text[:400]
    assert slug not in csv_response.text.splitlines()[0], "header leak"


# ----------------------------------------------------------------------- plan


@given("a YAML document:", target_fixture="document")
def document(docstring: str) -> str:
    return docstring


@given(parsers.parse('a YAML document creating "{slug}"'), target_fixture="document")
def document_creating(slug: str) -> str:
    return f"""gateways:
  - slug: {slug}
    name: {slug}
    target_url: http://backend.test/mcp
"""


@given(parsers.parse('a YAML document with prune enabled omitting "{slug}"'),
       target_fixture="document")
def document_pruning(slug: str) -> str:
    del slug
    return """gateways:
  - slug: something-else
    name: Something else
    target_url: http://backend.test/mcp
prune: true
"""


@when("the owner asks what applying it would do", target_fixture="plan")
def ask_plan(owner: dict, document: str) -> dict:
    response = owner["browser"].post("/api/config/plan", json={"yaml": document})
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse('the plan would create "{slug}"'))
def plan_creates(plan: dict, slug: str) -> None:
    assert slug in plan["create"], plan


@then("the plan would delete nothing")
def plan_deletes_nothing(plan: dict) -> None:
    assert plan["delete"] == [], plan


@then(parsers.parse('the plan would delete "{slug}"'))
def plan_deletes(plan: dict, slug: str) -> None:
    assert slug in plan["delete"], plan


@then(parsers.parse('gateway "{slug}" still exists'))
def gateway_still_there(owner: dict, slug: str) -> None:
    response = owner["browser"].get(f"/api/gateways?slug={slug}")
    assert response.status_code == 200, response.text
    assert response.json()["gateways"], response.text


# ---------------------------------------------------- config-as-code validation


def test_config_validate_never_echoes_the_environment(owner, monkeypatch) -> None:
    """`/config/validate` reads a document and echoes what it parsed.

    Expanding `${VAR}` anywhere in that document made the screen a way to read
    the process environment: the value came back in the response. The trap below
    is a variable that is set, so a passing test cannot be an unset variable
    quietly failing instead.
    """
    monkeypatch.setenv("LEAK_CANARY", "canary-secret-value")
    documents = [
        # Each field that is echoed or stored, one per case.
        "gateways:\n  - slug: ${LEAK_CANARY}\n    target_url: http://x/mcp\n",
        "gateways:\n  - slug: demo\n    name: ${LEAK_CANARY}\n    target_url: http://x\n",
        "gateways:\n  - slug: demo\n    target_url: ${LEAK_CANARY}\n",
        "gateways:\n  - slug: demo\n    target_url: http://x\n"
        "    description: ${LEAK_CANARY}\n",
        "roles:\n  - name: ${LEAK_CANARY}\n    permissions: [traffic:read]\n",
        "members:\n  - email: ${LEAK_CANARY}@example.dev\n",
    ]
    for text in documents:
        response = owner["browser"].post("/api/config/validate", json={"yaml": text})
        assert response.status_code == 422, (text, response.text)
        assert "canary-secret-value" not in response.text, (text, response.text)

    # A credential field still expands — and its value still must not come back.
    response = owner["browser"].post(
        "/api/config/validate",
        json={
            "yaml": "gateways:\n  - slug: demo\n    target_url: http://x\n"
            "    auth:\n      mode: api_key\n"
            "      upstream_token: ${LEAK_CANARY}\n"
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["gateways"] == ["demo"], response.text
    assert "canary-secret-value" not in response.text, response.text
