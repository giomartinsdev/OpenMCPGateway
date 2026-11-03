"""Steps for percentiles, key last-use, the audit CSV, history/restore and drift.

The read-side scenarios (percentiles, last use, CSV, history) call the service or
the HTTP API directly: there is no proxy hop in them, and a proxy round trip only
to inspect a column would make the assertion wait on the recorder. The drift
scenario is the exception — it goes through a real handshake, because the thing
under test is what the prober learns from a real upstream.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import BackendServer, owner_account

scenarios("../features/insight.feature")

PASSWORD = "correct-horse-battery"
OWNER = "insight-http@example.dev"
VIEWER = "insight-viewer@example.dev"

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
    from tests.steps.support import SecureCookieClient

    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield owner_account(client, OWNER)
    finally:
        client.close()

@pytest.fixture()
def viewer(live_server: str) -> dict[str, Any]:
    from tests.steps.support import SecureCookieClient, register

    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield register(client, VIEWER)
    finally:
        client.close()

@pytest.fixture()
def issued() -> dict[str, str]:
    return {}

# ------------------------------------------------------------------- gateways

def _create(owner: dict, slug: str, target: str) -> dict:
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

@given(parsers.parse('a gateway "{slug}" pointing at "{target}"'),
       target_fixture="gateway")
def plain_gateway(owner: dict, slug: str, target: str) -> dict:
    return _create(owner, slug, target)

@given("an API key was issued for the gateway", target_fixture="issued")
def issue_key(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/keys", json={"label": "test"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"plaintext": body["api_key"], "key_id": body["key_id"]}

# ------------------------------------------------------------------ percentiles

@given(parsers.parse("{count:d} observations with durations 1 to 100 milliseconds"))
def observations(container, runner, gateway: dict, count: int) -> None:
    """Insert straight into the projection: the distribution is what is under
    test, and 100 real MCP round trips would measure the loopback, not the code."""
    async def insert() -> None:
        from gateway.domain.events import RequestObserved

        for index in range(1, count + 1):
            event = RequestObserved(
                aggregate_id=f"traffic_{gateway['id']}",
                gateway_id=gateway["id"],
                subject="api-key:dist",
                outcome="allowed",
                status=200,
                duration_ms=index,
                tool="echo",
                occurred_at=datetime.now(UTC),
            )
            await container.projections.apply(0, event)

    runner.run(insert())

@when("the operator reads the stats for the last hour", target_fixture="stats")
def read_stats(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].get(
        f"/api/stats?minutes=60&gateway_id={gateway['id']}"
    )
    assert response.status_code == 200, response.text
    return response.json()

@then(parsers.parse("the p50 latency is {value:g} milliseconds"))
def p50(stats: dict, value: float) -> None:
    assert stats["p50_duration_ms"] == value, stats

@then(parsers.parse("the p95 latency is {value:g} milliseconds"))
def p95(stats: dict, value: float) -> None:
    assert stats["p95_duration_ms"] == value, stats

@then(parsers.parse("the p99 latency is {value:g} milliseconds"))
def p99(stats: dict, value: float) -> None:
    assert stats["p99_duration_ms"] == value, stats

# --------------------------------------------------------------------- last use

def _key_row(owner: dict, gateway: dict, key_id: str) -> dict:
    response = owner["browser"].get(f"/api/gateways/{gateway['id']}/keys")
    assert response.status_code == 200, response.text
    rows = [row for row in response.json()["keys"] if row["key_id"] == key_id]
    assert rows, response.json()
    return rows[0]

@when("a caller with the issued key calls it once", target_fixture="response")
def call_once(live_server: str, gateway: dict, issued: dict):
    from tests.steps.support import call_mcp

    return call_mcp(live_server, gateway["slug"], issued["plaintext"])

@then("the key's last-used time is recorded")
def last_used(container, runner, gateway: dict, issued: dict) -> None:
    async def wait() -> dict:
        for _ in range(100):
            row = await container.read_models.api_keys(gateway["id"])
            wanted = [r for r in row if r["key_id"] == issued["key_id"]]
            if wanted and wanted[0]["last_used_at"]:
                return wanted[0]
            await asyncio.sleep(0.05)
        return wanted[0] if wanted else {}

    row = runner.run(wait())
    assert row and row["last_used_at"], row

@then("the key has never been used")
def never_used(owner: dict, gateway: dict, issued: dict) -> None:
    row = _key_row(owner, gateway, issued["key_id"])
    assert row["last_used_at"] is None, row

# --------------------------------------------------------------------- audit CSV

@given(parsers.parse('a custom role "{name}" that may only read gateways'),
       target_fixture="role")
def custom_role(owner: dict, name: str) -> str:
    response = owner["browser"].post(
        "/api/roles", json={"name": name, "permissions": ["gateways:read"]}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]

@given(parsers.parse('an operator "{email}" with the role "{name}"'),
       target_fixture="other")
def role_account(owner: dict, role: str, email: str) -> dict[str, Any]:
    """A second account in the same tenant, holding a role that cannot audit."""
    from tests.steps.support import SecureCookieClient

    response = owner["browser"].post(
        "/api/operators",
        json={"email": email, "password": PASSWORD, "role_ids": [role]},
    )
    assert response.status_code == 201, response.text

    client = SecureCookieClient(base_url=owner["browser"].base_url,
                                follow_redirects=False)
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"browser": client, "email": email}

@when("the operator exports the audit log as CSV", target_fixture="csv")
def export_events(owner: dict) -> dict[str, Any]:
    response = owner["browser"].get("/api/events.csv?limit=200")
    return {"status": response.status_code, "body": response.text}

@when(parsers.parse('"{email}" exports the audit log as CSV'), target_fixture="csv")
def export_events_as(other: dict, email: str) -> dict[str, Any]:
    response = other["browser"].get("/api/events.csv?limit=200")
    return {"status": response.status_code, "body": response.text}

@then(parsers.parse('the CSV header starts with "{header}"'))
def csv_header(csv: dict, header: str) -> None:
    assert csv["body"].split("\r\n")[0].startswith(header), csv["body"][:200]

@then("the CSV contains the gateway's creation event")
def csv_has_created(csv: dict) -> None:
    assert "gateway.created" in csv["body"], csv["body"][:400]

@then("the CSV contains no event from another tenant")
def csv_scoped(csv: dict) -> None:
    # The fixture tenant is the only one in these scenarios; asserting the
    # absence of a foreign aggregate is what proves `owned_aggregates` was applied.
    assert "tn_other" not in csv["body"], csv["body"][:400]

@then(parsers.parse("the CSV export answers {status:d}"))
def answer_status(csv: dict, status: int) -> None:
    assert csv["status"] == status, csv

# --------------------------------------------------------------------- history

@given(parsers.parse('the operator renames it to "{name}"'))
def rename(owner: dict, gateway: dict, name: str) -> None:
    response = owner["browser"].patch(
        f"/api/gateways/{gateway['id']}", json={"name": name}
    )
    assert response.status_code == 200, response.text

@when("the operator reads the gateway's history", target_fixture="history")
def read_history(owner: dict, gateway: dict) -> list[dict]:
    response = owner["browser"].get(f"/api/gateways/{gateway['id']}/history")
    assert response.status_code == 200, response.text
    return response.json()["history"]

@then(parsers.parse("the answer is {status:d}"))
def restore_status(restore: dict, status: int) -> None:
    assert restore["status"] == status, restore

@then("the history has a version for every change")
def history_versions(history: list[dict]) -> None:
    assert len(history) == 3, history
    assert [entry["sequence"] for entry in history] == [1, 2, 3], history

@then("the last entry's actor is the operator who made it")
def history_actor(history: list[dict]) -> None:
    assert history[-1]["actor"] == OWNER, history[-1]

@when(parsers.parse("the operator restores the gateway to version {version:d}"),
       target_fixture="restore")
def restore(owner: dict, gateway: dict, version: int) -> dict[str, Any]:
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/restore", json={"version": version}
    )
    return {"status": response.status_code, "body": response.text}

@when("the operator restores the gateway to its current version",
      target_fixture="restore")
def restore_current(owner: dict, gateway: dict) -> dict[str, Any]:
    current = owner["browser"].get(f"/api/gateways/{gateway['id']}").json()
    history = owner["browser"].get(
        f"/api/gateways/{gateway['id']}/history"
    ).json()["history"]
    version = history[-1]["sequence"] if isinstance(history[-1], dict) else 1
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/restore", json={"version": version}
    )
    assert current  # the detail read is what the panel would show
    return {"status": response.status_code, "body": response.text}

@then(parsers.parse('the gateway is named "{name}"'))
def named(owner: dict, gateway: dict, name: str) -> None:
    response = owner["browser"].get(f"/api/gateways/{gateway['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == name, response.json()

@then("the restore is recorded as its own event")
def restore_event(container, runner, gateway: dict) -> None:
    async def events() -> list[dict]:
        return await container.store.load_stream(gateway["id"])

    stream = runner.run(events())
    assert any(event.EVENT == "gateway.restored" for event in stream), [
        event.EVENT for event in stream
    ]

# ------------------------------------------------------------------- drift

@given(
    parsers.parse('a stored fingerprint for tool "{tool}" that does not match the upstream'),
)
def stored_fingerprint(container, runner, gateway: dict, tool: str) -> None:
    async def store() -> None:
        from sqlalchemy import insert

        from gateway.infrastructure.db.tables import rm_tool_fingerprints

        async with container.database.session() as session, session.begin():
            await session.execute(
                insert(rm_tool_fingerprints).values(
                    gateway_id=gateway["id"],
                    tool=tool,
                    fingerprint="sha256:stale",
                )
            )

    runner.run(store())

@when("the operator tests the gateway's upstream", target_fixture="health")
def probe_upstream(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].post(f"/api/gateways/{gateway['id']}/test")
    assert response.status_code == 200, response.text
    return response.json()

@then(parsers.parse('the test reports the tool "{tool}" as changed'))
def tool_changed(health: dict, tool: str) -> None:
    assert tool in health["changed"], health

@then("the test reports no changed tools")
def no_drift(health: dict) -> None:
    # The first probe records fingerprints; it does not report a baseline as a
    # change, because a badge that is red on every fresh deployment is wallpaper.
    assert health["changed"] == [], health
