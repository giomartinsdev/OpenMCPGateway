"""Steps for the connect guide.

The assertions are about *shape and content of the instructions*, not styling:
the panel renders whatever this returns, so a wrong step order here is a wrong
step order on screen.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient, owner_account

scenarios("../features/connect_guide.feature")


@pytest.fixture()
def owner(live_server: str):
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield owner_account(client, "owner@example.dev")
    finally:
        client.close()


@pytest.fixture()
def other(live_server: str):
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield owner_account(client, "other@example.dev", tenant="Other Co")
    finally:
        client.close()


@pytest.fixture()
def guide() -> dict:
    return {}


def _create(owner: dict, slug: str, **auth) -> dict:
    response = owner["browser"].post(
        "/api/gateways",
        json={"slug": slug, "name": slug, "target_url": "http://up.test/mcp", **auth},
    )
    assert response.status_code == 201, response.text
    return response.json()


# -- given -----------------------------------------------------------------


@given(parsers.parse('an owner with a public gateway "{slug}"'), target_fixture="gateway")
def public_gateway(owner: dict, slug: str) -> dict:
    return _create(owner, slug)


@given(parsers.parse('an owner with an api_key gateway "{slug}"'), target_fixture="gateway")
def api_key_gateway(owner: dict, slug: str) -> dict:
    created = _create(owner, slug, auth_mode="api_key")
    issued = owner["browser"].post(
        f"/api/gateways/{created['id']}/keys", json={"label": "manual"}
    )
    assert issued.status_code == 201, issued.text
    return created


@given(
    parsers.parse('an owner with an oauth github gateway "{slug}"'),
    target_fixture="gateway",
)
def oauth_gateway(owner: dict, slug: str) -> dict:
    created = _create(owner, slug, auth_mode="oauth", idp="github")
    configured = owner["browser"].put(
        f"/api/gateways/{created['id']}/auth",
        json={
            "auth_mode": "oauth",
            "idp": "github",
            "client_id": "Ov23liTestClientId",
            "client_secret": "test-client-secret-value-32-bytes",
            "required_scopes": ["read:user"],
        },
    )
    assert configured.status_code == 200, configured.text
    return created


@given("someone else in another tenant")
def someone_else(other: dict) -> None:
    return None


# -- when ------------------------------------------------------------------


@when(
    parsers.parse('the owner reads the connect guide for "{slug}"'),
    target_fixture="guide_response",
)
def owner_reads_guide(owner: dict, gateway: dict) -> httpx.Response:
    return owner["browser"].get(f"/api/gateways/{gateway['id']}/connect")


@when(
    parsers.parse('the other tenant reads the connect guide for "{slug}"'),
    target_fixture="guide_response",
)
def other_reads_guide(other: dict, gateway: dict) -> httpx.Response:
    return other["browser"].get(f"/api/gateways/{gateway['id']}/connect")


# -- then ------------------------------------------------------------------


def _guide(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse("the guide offers {count:d} curl step"))
def curl_step_one(guide_response: httpx.Response, count: int) -> None:
    steps = _guide(guide_response)["curl"]
    assert len(steps) == count, [step["title"] for step in steps]


@then(parsers.parse("the guide offers {count:d} curl steps"))
def curl_steps(guide_response: httpx.Response, count: int) -> None:
    steps = _guide(guide_response)["curl"]
    assert len(steps) == count, [step["title"] for step in steps]


@then("the guide offers a python step")
def python_step(guide_response: httpx.Response) -> None:
    steps = _guide(guide_response)["python"]
    assert steps and "ClientSession" in steps[0]["code"], steps


@then("the curl step posts an MCP initialize to the gateway endpoint")
def posts_initialize(guide_response: httpx.Response) -> None:
    guide = _guide(guide_response)
    step = guide["curl"][0]
    assert "-X POST" in step["code"], step["code"]
    assert guide["endpoint"] in step["code"], step["code"]
    assert '"method": "initialize"' in step["code"], step["code"]


@then("the curl step sends a bearer token")
def sends_bearer(guide_response: httpx.Response) -> None:
    step = _guide(guide_response)["curl"][0]
    assert 'Authorization: Bearer' in step["code"], step["code"]


@then(parsers.parse('the curl step does not contain "{secret}"'))
def no_secret(guide_response: httpx.Response, secret: str) -> None:
    assert secret not in _guide(guide_response)["curl"][0]["code"]


@then("the steps start with an unauthenticated challenge")
def starts_with_challenge(guide_response: httpx.Response) -> None:
    steps = _guide(guide_response)["curl"]
    first = steps[0]["code"]
    assert '"method": "initialize"' in first, first
    assert "Authorization" not in first, first


@then("a step fetches the protected resource metadata")
def fetches_metadata(guide_response: httpx.Response) -> None:
    codes = " ".join(step["code"] for step in _guide(guide_response)["curl"])
    assert "/.well-known/oauth-protected-resource/g/" in codes, codes


@then("a step registers a client")
def registers_client(guide_response: httpx.Response) -> None:
    codes = " ".join(step["code"] for step in _guide(guide_response)["curl"])
    assert "/register" in codes, codes


@then("a step exchanges the code for a token")
def exchanges_code(guide_response: httpx.Response) -> None:
    codes = " ".join(step["code"] for step in _guide(guide_response)["curl"])
    assert "grant_type=authorization_code" in codes, codes


@then("the last step sends a bearer token")
def last_step_bearer(guide_response: httpx.Response) -> None:
    last = _guide(guide_response)["curl"][-1]["code"]
    assert "Authorization: Bearer $ACCESS_TOKEN" in last, last


@then("the python guide runs the flow through the SDK")
def python_sdk(guide_response: httpx.Response) -> None:
    steps = _guide(guide_response)["python"]
    assert any("streamable_http_client" in step["code"] for step in steps), steps


@then("the guide request is not found")
def not_found(guide_response: httpx.Response) -> None:
    assert guide_response.status_code == 404, guide_response.text
