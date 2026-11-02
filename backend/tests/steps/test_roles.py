"""Steps for roles, permissions and granting them.

Every assertion goes through the HTTP API with a real session cookie, not against
the command handler: what matters is that the *route* refuses, since that is the
thing a hidden button cannot enforce.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient, owner_account

scenarios("../features/roles.feature")

PASSWORD = "correct-horse-battery"
OWNER = "owner@example.dev"

@pytest.fixture()
def owner(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@pytest.fixture()
def outsider(live_server: str) -> httpx.Client:
    """A second cookie jar: the member's own view, kept apart from the owner's."""
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@pytest.fixture()
def ctx() -> dict:
    return {}

def _register(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD}
    )

def _login(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )

def _roles(browser: httpx.Client) -> dict[str, dict]:
    """By name, plus by id: a feature names a role either way."""
    response = browser.get("/api/roles")
    assert response.status_code == 200, response.text
    roles = response.json()["roles"]
    return {**{role["id"]: role for role in roles},
            **{role["name"]: role for role in roles}}

def _define(browser: httpx.Client, name: str, permissions: str) -> httpx.Response:
    return browser.post(
        "/api/roles",
        json={
            "name": name,
            "permissions": [p.strip() for p in permissions.split(",") if p.strip()],
        },
    )

def _add_member(browser: httpx.Client, email: str,
                role_ids: list[str] | None = None) -> httpx.Response:
    return browser.post(
        "/api/operators",
        json={"email": email, "password": PASSWORD, "role_ids": role_ids or []},
    )

def _whoami(browser: httpx.Client) -> dict:
    response = browser.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()

def _sign_in(ctx: dict, browser: httpx.Client, email: str) -> httpx.Client:
    assert _login(browser, email).status_code == 200, f"{email} could not sign in"
    return browser

# -- given -----------------------------------------------------------------

@given("the owner registered", target_fixture="owner_created")
def owner_registered(owner: httpx.Client) -> dict:
    return owner_account(owner, OWNER)

@given(parsers.parse('the administrator added the member "{email}"'),
       target_fixture="member")
def member_added(owner_created: dict, outsider: httpx.Client, email: str) -> dict:
    response = _add_member(owner_created["browser"], email)
    assert response.status_code == 201, response.text
    _sign_in({}, outsider, email)
    return {"browser": outsider, "email": email, **response.json()}

@given(parsers.parse('the administrator defined the role "{name}" with "{permissions}"'),
       target_fixture="defined")
def role_defined(owner_created: dict, name: str, permissions: str) -> dict:
    return _define(owner_created["browser"], name, permissions)

@given(parsers.parse('the administrator added the member "{email}" with the role "{role}"'),
       target_fixture="member")
def member_added_with_role(owner_created: dict, outsider: httpx.Client, email: str,
                           role: str) -> dict:
    defined = _roles(owner_created["browser"])[role]
    response = _add_member(owner_created["browser"], email, [defined["id"]])
    assert response.status_code == 201, response.text
    _sign_in({}, outsider, email)
    return {"browser": outsider, "email": email, **response.json()}

# -- when ------------------------------------------------------------------

@when("the owner reads who it is", target_fixture="identity")
def owner_reads(owner_created: dict) -> dict:
    return _whoami(owner_created["browser"])

@when(parsers.parse('the administrator defines the role "{name}" with "{permissions}"'),
      target_fixture="defined")
def administrator_defines(owner_created: dict, name: str, permissions: str) -> dict:
    return _define(owner_created["browser"], name, permissions)

@when(parsers.parse('the administrator adds the member "{email}"'),
      target_fixture="member")
def administrator_adds_member(owner_created: dict, outsider: httpx.Client,
                              email: str) -> dict:
    return member_added(owner_created, outsider, email)

@when(parsers.parse('the administrator adds the member "{email}" with the role "{role}"'),
      target_fixture="member")
def administrator_adds_with_role(owner_created: dict, outsider: httpx.Client,
                                 email: str, role: str) -> dict:
    return member_added_with_role(owner_created, outsider, email, role)

@when("that member creates a gateway", target_fixture="attempt")
def member_creates_gateway(member: dict) -> httpx.Response:
    return member["browser"].post(
        "/api/gateways",
        json={"slug": "by-member", "name": "By member", "target_url": "http://up.test/mcp"},
    )

@when(parsers.parse('the member tries to create a gateway'), target_fixture="attempt")
def member_tries_to_create(member: dict) -> httpx.Response:
    return member["browser"].post(
        "/api/gateways",
        json={"slug": "nope", "name": "Nope", "target_url": "http://up.test/mcp"},
    )

@when(parsers.parse('that member adds a new member "{email}" with no role'),
      target_fixture="new_member_response")
def member_adds_member(member: dict, email: str) -> httpx.Response:
    return _add_member(member["browser"], email)

@when(parsers.parse('the administrator gives that member the role "{role}"'),
      target_fixture="member")
def administrator_grants(owner_created: dict, member: dict,
                         role: str) -> dict:
    target = _roles(owner_created["browser"])[role]
    response = owner_created["browser"].put(
        f"/api/operators/{member['operator_id']}/roles",
        json={"role_ids": [target["id"]]},
    )
    assert response.status_code == 200, response.text
    # The member's session is already valid; a role change needs no re-login
    # because permissions are resolved per request.
    return member

@when("that member reads who it is", target_fixture="identity")
def member_reads(member: dict) -> dict:
    return _whoami(member["browser"])

@when(parsers.parse('that member adds a new member "{email}" with the role "{role}"'),
      target_fixture="attempt")
def member_adds_with_role(member: dict, email: str, role: str) -> httpx.Response:
    return _add_member(member["browser"], email, [role])

@when(parsers.parse('the administrator deletes the role "{role}"'),
      target_fixture="attempt")
def administrator_deletes(owner_created: dict, role: str) -> httpx.Response:
    known = _roles(owner_created["browser"])
    role_id = known[role]["id"] if role in known else role
    return owner_created["browser"].delete(f"/api/roles/{role_id}")

# -- then ------------------------------------------------------------------

@then(parsers.parse('the owner has the "{role_id}" role'))
def owner_has_role(identity: dict, role_id: str) -> None:
    assert identity["role_ids"] == [role_id], identity

@then("the owner may write roles")
def owner_writes_roles(identity: dict) -> None:
    assert "roles:write" in identity["permissions"], identity


@then("the member may write roles")
def member_writes_roles(identity: dict) -> None:
    assert "roles:write" in identity["permissions"], identity

@then(parsers.parse('the role "{name}" grants exactly "{permissions}"'))
def role_grants(owner_created: dict, name: str, permissions: str) -> None:
    role = _roles(owner_created["browser"])[name]
    expected = [p.strip() for p in permissions.split(",") if p.strip()]
    assert role["permissions"] == expected, role

@then(parsers.parse('defining the role fails naming "{permission}"'))
def define_fails(owner_created: dict, permission: str) -> None:
    response = _define(owner_created["browser"], "Typo", permission)
    assert response.status_code == 422, response.text
    assert permission in response.text, response.text

@then(parsers.parse('the member has the "{role_id}" role'))
def member_has_role(member: dict, owner_created: dict, role_id: str) -> None:
    response = owner_created["browser"].get("/api/operators")
    assert response.status_code == 200, response.text
    operators = {op["email"]: op for op in response.json()["operators"]}
    assert operators[member["email"]]["role_ids"] == [role_id], operators

@then("that member cannot define a role")
def member_cannot_define(member: dict) -> None:
    response = _define(member["browser"], "Sneaky", "roles:write")
    assert response.status_code == 403, response.text

@then("the attempt is forbidden")
def attempt_forbidden(attempt: httpx.Response) -> None:
    assert attempt.status_code == 403, attempt.text

@then("the member may read traffic")
def member_reads_traffic(member: dict) -> None:
    assert member["browser"].get("/api/stats").status_code == 200

@then("the gateway exists")
def gateway_exists(attempt: httpx.Response, owner_created: dict) -> None:
    assert attempt.status_code == 201, attempt.text
    listing = owner_created["browser"].get("/api/gateways").json()["gateways"]
    assert "by-member" in [g["slug"] for g in listing], listing

@then(parsers.parse('the new member has the "{role_id}" role'))
def new_member_role(new_member_response: httpx.Response, role_id: str) -> None:
    assert new_member_response.status_code == 201, new_member_response.text
    assert new_member_response.json()["role_ids"] == [role_id], (
        new_member_response.json()
    )

@then(parsers.parse('the attempt fails naming "{fragment}"'))
def attempt_fails_naming(attempt: httpx.Response, fragment: str) -> None:
    assert attempt.status_code == 422, attempt.text
    assert fragment in attempt.json()["detail"], attempt.text

@then("the deletion fails with a reason")
def deletion_fails(attempt: httpx.Response) -> None:
    assert attempt.status_code == 422, attempt.text
    assert attempt.json()["detail"], attempt.text
