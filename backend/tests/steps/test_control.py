"""Steps for request ids, monthly quota, member revocation and webhooks.

The proxy scenarios run against a real FastMCP backend because the guard is what
is under test; the webhook ones need a real HTTP receiver, so they get one on a
port rather than a stub — a signature the receiver never verifies is a signature
nobody has checked.
"""

import asyncio
import hashlib
import hmac
import threading
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import (
    BackendServer,
    SecureCookieClient,
    call_mcp,
    owner_account,
)

scenarios("../features/control.feature")

PASSWORD = "correct-horse-battery"
OWNER = "control-http@example.dev"
HOOK_SECRET = "hook-secret-at-least-32-bytes!!"

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

def _rows(
    container, runner, gateway: dict, *,
    until: Any = None, timeout: float = 5.0,
) -> list[dict]:
    """The gateway's traffic rows, polled until the recorder has caught up.

    The recorder writes off the hot path, so an assertion that needs a row pays
    for its own wait instead of sleeping a fixed amount. `until` narrows the wait
    to the row the step is actually about.
    """
    async def wait() -> list[dict]:
        deadline = asyncio.get_running_loop().time() + timeout
        found: list[dict] = []
        while asyncio.get_running_loop().time() < deadline:
            found = await container.read_models.request_tail(gateway["id"], 50)
            if found and (until is None or any(until(row) for row in found)):
                return found
            await asyncio.sleep(0.05)
        return found

    return runner.run(wait())

class Receiver:
    """A real HTTP endpoint that records what arrived.

    Started in a thread with `http.server`: the webhook client is asyncio, and a
    test double that shares its loop would hide exactly the failure mode the
    retry exists for — a receiver that is slow or refusing.
    """

    def __init__(self, status: int = 200) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.calls: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                outer.calls.append(
                    {
                        "body": self.rfile.read(length).decode(),
                        "signature": self.headers.get("x-omg-signature", ""),
                    }
                )
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    def close(self) -> None:
        self.server.shutdown()

@pytest.fixture()
def receiver() -> Receiver:
    endpoint = Receiver()
    try:
        yield endpoint
    finally:
        endpoint.close()

@pytest.fixture()
def failing_receiver() -> Receiver:
    endpoint = Receiver(status=500)
    try:
        yield endpoint
    finally:
        endpoint.close()

# ------------------------------------------------------------------- gateways

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
        f"/api/gateways/{gateway['id']}/keys", json={"label": "test"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"plaintext": body["api_key"], "key_id": body["key_id"]}

# ------------------------------------------------------------------ request id

@when(
    parsers.parse('a caller with the issued key calls it with request id "{request_id}"'),
    target_fixture="response",
)
def call_with_id(live_server: str, gateway: dict, issued: dict, request_id: str):
    return call_mcp(
        live_server, gateway["slug"], issued["plaintext"], request_id=request_id
    )

@when("a caller with the issued key calls it once", target_fixture="response")
def call_once(live_server: str, gateway: dict, issued: dict):
    return call_mcp(live_server, gateway["slug"], issued["plaintext"])

@when(
    parsers.parse('an anonymous caller probes the gateway with request id "{request_id}"'),
    target_fixture="response",
)
def anonymous_with_id(live_server: str, gateway: dict, request_id: str):
    return call_mcp(live_server, gateway["slug"], None, request_id=request_id)

@then(parsers.parse('the response carries request id "{request_id}"'))
def echoed(response, request_id: str) -> None:
    assert response.headers.get("x-request-id") == request_id, dict(response.headers)

@then("the response carries a generated request id")
def generated(response) -> None:
    value = response.headers.get("x-request-id", "")
    # Generated, not empty: two callers who sent no header must not look like the
    # same call in the log.
    assert value.startswith("req_"), dict(response.headers)

@then(parsers.parse('the traffic log records request id "{request_id}"'))
def logged_id(container, runner, gateway: dict, request_id: str) -> None:
    rows = _rows(container, runner, gateway)
    assert any(row["request_id"] == request_id for row in rows), rows[:3]

@then("the traffic log records a request id")
def logged_any_id(container, runner, gateway: dict) -> None:
    rows = _rows(container, runner, gateway)
    assert rows and rows[0].get("request_id"), rows[:3]

# ---------------------------------------------------------------- monthly quota

@given(parsers.parse("the gateway's monthly quota is {quota:d} call"),
       target_fixture="quota")
def monthly_quota(owner: dict, gateway: dict, quota: int) -> int:
    response = owner["browser"].put(
        f"/api/gateways/{gateway['id']}/quota", json={"monthly_quota": quota}
    )
    assert response.status_code == 200, response.text
    return quota

@given("the gateway's monthly quota is 0 calls", target_fixture="quota")
def unlimited_quota(owner: dict, gateway: dict) -> int:
    response = owner["browser"].put(
        f"/api/gateways/{gateway['id']}/quota", json={"monthly_quota": 0}
    )
    assert response.status_code == 200, response.text
    return 0

@given("the quota counter was last rolled over in September")
def stale_counter(container, runner, gateway: dict) -> None:
    """Move the stored month back, which is what a clock says on the first.

    The window is a calendar month in UTC; a counter that never rolls over turns
    "the first call of the month" into a 429 nobody can explain.
    """
    async def backdate() -> None:
        from sqlalchemy import update

        from gateway.infrastructure.db.tables import rm_gateways

        async with container.database.session() as session, session.begin():
            await session.execute(
                update(rm_gateways)
                .where(rm_gateways.c.id == gateway["id"])
                .values(quota_month="2026-09", requests_this_month=99)
            )

    runner.run(backdate())

@when(parsers.parse("a caller with the issued key calls it {count:d} times"),
      target_fixture="probe")
def n_calls(live_server: str, gateway: dict, issued: dict, count: int) -> dict:
    statuses = [
        call_mcp(live_server, gateway["slug"], issued["plaintext"]).status_code
        for _ in range(count)
    ]
    return {"statuses": statuses}

@then("one call is refused for the monthly quota")
def refused_quota(container, runner, gateway: dict, probe: dict) -> None:
    assert 429 in probe["statuses"], probe
    # Poll for the refusal specifically: the recorder may have written the
    # allowed row first, and a wait for "any row" would read the wrong one.
    rows = _rows(container, runner, gateway,
                 until=lambda row: row["reason"] == "monthly quota exhausted")
    assert any(row["reason"] == "monthly quota exhausted" for row in rows), rows[:3]

@then("no call is refused for the monthly quota")
def not_refused_quota(container, runner, gateway: dict, probe: dict) -> None:
    rows = _rows(container, runner, gateway)
    assert all(status == 200 for status in probe["statuses"]), probe
    assert all(row["reason"] != "monthly quota exhausted" for row in rows), rows[:3]

@then("the call is allowed")
def call_allowed(response) -> None:
    assert response.status_code == 200, response.text

# ------------------------------------------------------------------ revocation

@given(parsers.parse('a member "{email}" with the viewer role'),
       target_fixture="member")
def member_with_role(owner: dict, email: str) -> dict[str, Any]:
    response = owner["browser"].post(
        "/api/operators",
        json={"email": email, "password": PASSWORD, "role_ids": ["role_viewer"]},
    )
    assert response.status_code == 201, response.text
    return {"operator_id": response.json()["operator_id"], "email": email}

@given("that member is signed in", target_fixture="signed_in")
def member_signed_in(live_server: str, member: dict) -> dict[str, Any]:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    response = client.post(
        "/api/auth/login", json={"email": member["email"], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"browser": client}

@when(parsers.parse('the owner disables "{email}"'))
def disable(owner: dict, member: dict, email: str) -> None:
    del email
    response = owner["browser"].post(f"/api/operators/{member['operator_id']}/disable")
    assert response.status_code == 200, response.text

@given(parsers.parse('the owner disabled "{email}"'))
def disabled_already(owner: dict, member: dict, email: str) -> None:
    disable(owner, member, email)

@when(parsers.parse('the owner re-enables "{email}"'))
def enable(owner: dict, member: dict, email: str) -> None:
    del email
    response = owner["browser"].post(f"/api/operators/{member['operator_id']}/enable")
    assert response.status_code == 200, response.text

@then("the member's next request is 401")
def next_request_401(signed_in: dict) -> None:
    response = signed_in["browser"].get("/api/auth/me")
    assert response.status_code == 401, response.text

@then(parsers.parse('signing in as "{email}" is refused'))
def login_refused(live_server: str, email: str) -> None:
    with SecureCookieClient(base_url=live_server) as client:
        response = client.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        )
    assert response.status_code == 401, response.text

@then(parsers.parse('signing in as "{email}" succeeds'))
def login_succeeds(live_server: str, email: str) -> None:
    with SecureCookieClient(base_url=live_server) as client:
        response = client.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        )
    assert response.status_code == 200, response.text

@then(parsers.parse('the audit log still names "{email}"'))
def audit_names(container, runner, email: str) -> None:
    async def events() -> list[dict]:
        return await container.store.recent_events(limit=200)

    rows = runner.run(events())
    assert any(row["payload"].get("email") == email for row in rows), rows[:3]

# ------------------------------------------------------------------- webhooks

@given("a webhook pointing at a real HTTP endpoint", target_fixture="webhook")
def webhook_to(owner: dict, gateway: dict, receiver: Receiver) -> dict[str, Any]:
    return _hook(owner, gateway, receiver.url, HOOK_SECRET)

@given("a webhook pointing at an endpoint that fails", target_fixture="webhook")
def webhook_failing(owner: dict, gateway: dict,
                    failing_receiver: Receiver) -> dict[str, Any]:
    return _hook(owner, gateway, failing_receiver.url, HOOK_SECRET)

@given(parsers.parse('a webhook subscribed to "{outcome}" only'),
       target_fixture="webhook")
def webhook_subscribed(owner: dict, gateway: dict, receiver: Receiver,
                       outcome: str) -> dict[str, Any]:
    return _hook(owner, gateway, receiver.url, HOOK_SECRET, outcomes=[outcome])

@given("a webhook with no secret", target_fixture="attempt")
def webhook_no_secret(gateway: dict) -> dict[str, Any]:
    return {"gateway_id": gateway["id"], "payload": {"url": "http://127.0.0.1:9/hook"}}

@given(parsers.parse('a webhook pointing at "{url}"'), target_fixture="attempt")
def webhook_url(gateway: dict, url: str) -> dict[str, Any]:
    return {
        "gateway_id": gateway["id"],
        "payload": {"url": url, "secret": HOOK_SECRET},
    }

def _hook(owner: dict, gateway: dict, url: str, secret: str,
          outcomes: list[str] | None = None) -> dict[str, Any]:
    response = owner["browser"].post(
        f"/api/gateways/{gateway['id']}/webhooks",
        json={"url": url, "secret": secret, "outcomes": outcomes or []},
    )
    assert response.status_code == 201, response.text
    return {**response.json(), "secret": secret}

@when("the gateway refuses a call for an anonymous caller")
def anonymous_refusal(live_server: str, gateway: dict) -> None:
    status = call_mcp(live_server, gateway["slug"], None).status_code
    assert status == 401, status

@then("the endpoint receives a POST with a signature")
def post_signed(runner, receiver: Receiver, webhook: dict) -> None:
    assert runner.run(_wait_for_delivery(receiver)), receiver.calls
    call = receiver.calls[0]
    expected = hmac.new(
        webhook["secret"].encode(), call["body"].encode(), hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(call["signature"], expected), call

@then("the payload names the gateway and the outcome")
def payload_names(receiver: Receiver, gateway: dict) -> None:
    payload = _json(receiver.calls[0]["body"])
    assert payload["gateway"] == gateway["slug"], payload
    assert payload["outcome"] == "unauthenticated", payload

@when("the gateway receives a failure observation")
def failure_observation(live_server: str, gateway: dict) -> None:
    status = call_mcp(live_server, gateway["slug"], None).status_code
    assert status == 401, status

@then("the delivery is retried")
def retried(runner, failing_receiver: Receiver) -> None:
    assert runner.run(_wait_for_delivery(failing_receiver, attempts=2)), (
        failing_receiver.calls
    )

@then("the delivery is recorded as failed")
def failed_recorded(container, runner, webhook: dict) -> None:
    async def status() -> dict:
        for _ in range(200):
            rows = await container.read_models.webhook_deliveries(webhook["id"], 10)
            failed = [entry for entry in rows if entry["status"] == "failed"]
            if failed:
                return failed[0]
            await asyncio.sleep(0.05)
        return {}

    assert runner.run(status()), webhook

@when("the gateway receives an allowed observation")
def allowed_observation(live_server: str, gateway: dict, issued: dict) -> None:
    status = call_mcp(
        live_server, gateway["slug"], issued["plaintext"]
    ).status_code
    assert status == 200, status

@then("the endpoint receives nothing")
def nothing(runner, receiver: Receiver) -> None:
    # Something has to have had time to arrive for "nothing here" to mean the
    # filter worked rather than the test being fast.
    runner.run(asyncio.sleep(0.6))
    assert receiver.calls == [], receiver.calls

@then(parsers.parse("the webhook is refused with {status:d}"))
def webhook_refused(owner: dict, attempt: dict, status: int) -> None:
    response = owner["browser"].post(
        f"/api/gateways/{attempt['gateway_id']}/webhooks", json=attempt["payload"]
    )
    assert response.status_code == status, response.text

async def _wait_for_delivery(receiver: Receiver, attempts: int = 1,
                             timeout: float = 10.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if len(receiver.calls) >= attempts:
            return True
        await asyncio.sleep(0.05)
    return False

def _json(body: str) -> dict[str, Any]:
    import json

    return json.loads(body)
