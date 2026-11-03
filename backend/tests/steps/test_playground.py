"""Steps for the tool playground.

The call goes through the real gateway over HTTP — same `/g/<slug>/mcp` a client
uses — because a playground with its own path would be a second, unguarded way
into a production server.
"""

import asyncio
import json
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import BackendServer, SecureCookieClient, owner_account

scenarios("../features/playground.feature")

PASSWORD = "correct-horse-battery"
OWNER = "play-http@example.dev"

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

def _create(owner: dict, slug: str, target: str) -> dict:
    browser = owner["browser"]
    response = browser.post(
        "/api/gateways",
        json={"slug": slug, "name": slug, "target_url": target,
              "auth_mode": "api_key"},
    )
    assert response.status_code == 201, response.text
    return browser.get(f"/api/gateways/{response.json()['id']}").json()

@given(
    parsers.parse('a gateway "{slug}" with api_key auth proxying a real MCP server'),
    target_fixture="gateway",
)
def live_gateway(owner: dict, backend: BackendServer, slug: str) -> dict:
    return _create(owner, slug, backend.url)

@given("an API key was issued for the gateway", target_fixture="issued")
def issue_key(owner: dict, gateway: dict) -> dict:
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/keys", json={"label": "play"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"plaintext": body["api_key"], "key_id": body["key_id"]}

@given(parsers.parse('the allowlist exposes only "{name}"'))
def only_tool(owner: dict, gateway: dict, name: str) -> None:
    response = owner["browser"].put(
        f"/api/gateways/{gateway['id']}/tools", json={"tools": [name]}
    )
    assert response.status_code == 200, response.text

@when(
    parsers.parse('the operator calls tool "{tool}" with arguments {arguments}'),
    target_fixture="result",
)
def call_tool(owner: dict, gateway: dict, tool: str, arguments: str) -> dict[str, Any]:
    """Send the arguments as text, the way the panel's textarea does.

    A string that is not JSON is a 422 from the API, not a value the upstream
    should have to explain; parsing here would hide which layer refused it.
    """
    try:
        parsed = json.loads(arguments)
    except ValueError:
        # A string that is not JSON is what the operator typed; the API must
        # refuse it with a 422, not pass it through for the upstream to explain.
        parsed = arguments
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/call",
        json={"tool": tool, "arguments": parsed},
    )
    return {"status": response.status_code, "body": response.text}

@then("the call succeeds")
def call_succeeds(result: dict) -> None:
    assert result["status"] == 200, result

@then(parsers.parse('the result contains "{text}"'))
def result_contains(result: dict, text: str) -> None:
    assert text in result["body"], result["body"][:400]

@then("the call is refused as forbidden")
def refused_forbidden(result: dict) -> None:
    assert result["status"] == 403, result

@then("the refusal names the tool allowlist")
def names_allowlist(result: dict) -> None:
    assert "tool not in allowlist" in result["body"], result["body"][:300]

@then("the call is refused as unauthenticated")
def refused_unauthenticated(result: dict) -> None:
    assert result["status"] == 401, result

@then("the call fails")
def call_fails(result: dict) -> None:
    assert result["status"] >= 400, result

@then(parsers.parse('the failure mentions "{text}"'))
def failure_mentions(result: dict, text: str) -> None:
    assert text in result["body"], result["body"][:300]

@then(parsers.parse("the answer is {status:d}"))
def answer(result: dict, status: int) -> None:
    assert result["status"] == status, result

@then(parsers.parse('the traffic log records a call to tool "{tool}"'))
def logged_tool(container, runner, gateway: dict, tool: str) -> None:
    async def wait() -> list[dict]:
        for _ in range(100):
            rows = await container.read_models.request_tail(gateway["id"], 20)
            if any(row["tool"] == tool for row in rows):
                return rows
            await asyncio.sleep(0.05)
        return []

    rows = runner.run(wait())
    assert any(row["tool"] == tool for row in rows), rows[:3]
