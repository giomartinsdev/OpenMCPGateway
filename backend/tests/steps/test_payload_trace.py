"""Steps for the payload trace: sizes, previews, body ceiling and caller IP.

The gateway here is the real one in front of a real FastMCP backend, so a wrong
byte count or a preview built from the wrong buffer shows up as a mismatch
against what actually crossed the wire — not against a fixture that agrees with
the implementation.
"""

import asyncio
import json

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.domain.events import TENANT_OF_DEPLOYMENT, AuthMode
from tests.steps.support import BackendServer, Context, call_mcp

scenarios("../features/payload_trace.feature")

OPERATOR_EMAIL = "ops@example.dev"
OPERATOR_PASSWORD = "correct-horse-battery"
#: Long enough to be cut by any sane cap, short enough to stay one HTTP request.
LONG_ARGUMENT = "x" * 4000
TOKEN = "ghp_thisisasecrettoken0000000000000000"

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

@given(
    parsers.parse('a gateway "{slug}" with api_key auth proxying a real MCP server'),
    target_fixture="gateway",
)
def gateway_api_key(container, runner, operator: str, backend: BackendServer,
                    slug: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url=backend.url, actor=operator,
            auth_mode=AuthMode.API_KEY,
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id, TENANT_OF_DEPLOYMENT)

    return runner.run(create())

@given("an API key was issued for the gateway", target_fixture="issued_key")
def issued_key(container, runner, operator: str, gateway: dict) -> dict:
    async def issue() -> dict:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway["id"], label="trace", actor=operator
        )
        await container.refresh_router()
        return {"plaintext": plaintext, "key_id": key_id}

    return runner.run(issue())


def _latest_observation(container, runner, gateway: dict, *, status: int | None = None,
                        tool: str | None = None) -> dict:
    """Poll the traffic tail until the interesting observation lands.

    The recorder writes off the hot path, so "it has not arrived yet" and "it was
    never written" look identical without a bounded wait.

    `tool` selects the `tools/call` row rather than whichever row is newest: one
    MCP call is three HTTP posts, and rows written in the same millisecond come
    back in id order, not call order.
    """

    async def check() -> dict:
        for _ in range(120):
            rows = await container.read_models.request_tail(gateway["id"], 30)
            for row in rows:
                if tool is not None and row["tool"] != tool:
                    continue
                if status is not None and row["status"] != status:
                    continue
                return row
            await asyncio.sleep(0.05)
        return {}

    return runner.run(check())


# -- when -------------------------------------------------------------------


@when("a caller with the issued key calls it once", target_fixture="probe")
def call_once(live_server: str, gateway: dict, issued_key: dict) -> dict:
    response = call_mcp(
        live_server, gateway["slug"], issued_key["plaintext"], tool="echo",
        arguments={"text": "hello"},
    )
    return {"status": response.status_code, "body": response.text}


@when("a caller sends a tool call carrying a token", target_fixture="probe")
def call_with_token(live_server: str, gateway: dict, issued_key: dict) -> dict:
    response = call_mcp(
        live_server, gateway["slug"], issued_key["plaintext"], tool="echo",
        arguments={"text": "hi", "access_token": TOKEN},
    )
    return {"status": response.status_code, "body": response.text}


@when("a caller sends a long but legal argument", target_fixture="probe")
def call_long(live_server: str, gateway: dict, issued_key: dict) -> dict:
    response = call_mcp(
        live_server, gateway["slug"], issued_key["plaintext"], tool="echo",
        arguments={"text": LONG_ARGUMENT},
    )
    return {"status": response.status_code, "body": response.text}


@when("a caller sends a body larger than the limit", target_fixture="probe")
def call_oversized(live_server: str, gateway: dict, issued_key: dict) -> dict:
    """One POST, no handshake: the size is refused before MCP is spoken."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "x" * 2_000_000}},
        }
    )
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        content=payload.encode(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {issued_key['plaintext']}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    return {"status": response.status_code, "body": response.text}


@when(parsers.parse('a caller spoofs the forwarded-for header as "{ip}"'),
      target_fixture="probe")
def spoof_forwarded(live_server: str, gateway: dict, issued_key: dict, ip: str) -> dict:
    """The peer is 127.0.0.1; the header claims otherwise.

    `OMG_TRUSTED_PROXIES` is empty in this deployment, which is the default, so
    the claim is not an address.
    """
    response = httpx.post(
        f"{live_server}/g/{gateway['slug']}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "spoof", "version": "1"},
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {issued_key['plaintext']}",
            "X-Forwarded-For": ip,
        },
        timeout=20,
    )
    return {"status": response.status_code, "body": response.text, "claimed": ip}


# -- then -------------------------------------------------------------------


@then("the newest observation reports bytes in and out")
def reports_bytes(container, runner, gateway: dict) -> None:
    row = _latest_observation(container, runner, gateway, tool="echo")
    assert row, "no observation recorded"
    assert row["bytes_in"] > 0, row
    assert row["bytes_out"] > 0, row


@then("the observation's request preview names the tool")
def preview_names_tool(container, runner, gateway: dict) -> None:
    row = _latest_observation(container, runner, gateway, tool="echo")
    assert row, "no observation recorded"
    assert "echo" in row["request_preview"], row


@then("the observation's request preview does not contain the token")
def preview_redacts(container, runner, gateway: dict) -> None:
    row = _latest_observation(container, runner, gateway, tool="echo")
    assert row, "no observation recorded"
    assert TOKEN not in row["request_preview"], row
    assert "<redacted>" in row["request_preview"], row


@then("the observation's request preview is cut at the cap and says so")
def preview_truncated(container, runner, gateway: dict) -> None:
    row = _latest_observation(container, runner, gateway, tool="echo")
    assert row, "no observation recorded"
    preview = row["request_preview"]
    assert len(preview) <= 1024, len(preview)
    assert LONG_ARGUMENT not in preview, "the cap did not apply"
    assert preview.endswith("…"), repr(preview[-40:])


@then("the upstream server saw no request")
def upstream_untouched(backend: BackendServer) -> None:
    """The upstream records every bearer it is handed; none means none reached it."""
    assert backend.seen == [], backend.seen


@then(parsers.parse("the caller receives {status:d}"))
def receives_status(probe: dict, status: int) -> None:
    assert probe["status"] == status, f"{probe['status']}: {probe['body'][:400]}"


@then(parsers.parse('the newest observation records the peer address, not "{ip}"'))
def records_peer(container, runner, gateway: dict, probe: dict, ip: str) -> None:
    row = _latest_observation(container, runner, gateway)
    assert row, "no observation recorded"
    assert row["ip"] != ip, row
    assert row["ip"], row


@then(parsers.parse('the traffic log records outcome "{outcome}" with status {status:d}'))
def traffic_records(container, runner, gateway: dict, outcome: str, status: int) -> None:
    row = _latest_observation(container, runner, gateway, status=status)
    assert row, f"no observation with status {status}"
    assert row["outcome"] == outcome, row


@then("the traffic export has a bytes_in column and a bytes_out column")
def csv_has_byte_columns(live_server: str, gateway: dict) -> None:
    from tests.steps.support import SecureCookieClient

    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        signed = client.post(
            "/api/auth/login",
            json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD},
        )
        assert signed.status_code == 200, signed.text
        export = client.get("/api/requests.csv")
    finally:
        client.close()
    assert export.status_code == 200, export.text
    first = export.text.splitlines()[0].split(",")
    assert "bytes_in" in first, first
    assert "bytes_out" in first, first
