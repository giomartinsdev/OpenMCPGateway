"""Steps for tenants and their members.

Two browsers, not one: every scenario here is about what a *second* person sees,
so sharing a cookie jar would prove nothing.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient

scenarios("../features/tenant.feature")

PASSWORD = "correct-horse-battery"
OWNER = "owner@example.dev"
MEMBER = "member@example.dev"

@pytest.fixture()
def owner(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@pytest.fixture()
def member(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

def _register(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD}
    )

def _login(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )

# -- given / when ----------------------------------------------------------

@given("the first account registered", target_fixture="owner_created")
def first_account_registered(owner: httpx.Client) -> dict:
    response = _register(owner, OWNER)
    assert response.status_code == 201, response.text
    assert _login(owner, OWNER).status_code == 200, "owner could not sign in"
    return {"browser": owner, "email": OWNER, **response.json()}

@when("the owner reads who it is", target_fixture="owner_identity")
def owner_reads_self(owner_created: dict) -> dict:
    response = owner_created["browser"].get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()

@when(parsers.parse('the administrator adds the member "{email}"'),
      target_fixture="member_created")
def administrator_adds_member(owner_created: dict, member: httpx.Client,
                              email: str) -> dict:
    """An administrator adding a member, not open registration.

    Registration is closed after the first account on purpose: an open form on a
    self-hosted panel hands administration to anyone who finds the URL. The
    tenant is what makes a second account possible at all, so this is the path
    it is created by.
    """
    response = owner_created["browser"].post(
        "/api/operators", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    assert _login(member, email).status_code == 200, "member could not sign in"
    return {"browser": member, "email": email, **response.json()}

@given(parsers.parse('the administrator created a gateway "{slug}"'),
       target_fixture="gateway")
def administrator_gateway(owner_created: dict, slug: str) -> dict:
    response = owner_created["browser"].post(
        "/api/gateways",
        json={"slug": slug, "name": slug.title(), "target_url": "http://up.test/mcp"},
    )
    assert response.status_code == 201, response.text
    return response.json()

# -- then ------------------------------------------------------------------

@then("the owner is the tenant's administrator")
def owner_is_admin(owner_identity: dict, owner_created: dict) -> None:
    assert owner_identity["is_admin"] is True, owner_identity
    assert owner_identity["tenant_id"] == owner_created["tenant_id"], (
        owner_identity,
        owner_created,
    )

@then("both accounts belong to the same tenant")
def same_tenant(owner_identity: dict, member_created: dict) -> None:
    assert member_created["tenant_id"] == owner_identity["tenant_id"], (
        owner_identity,
        member_created,
    )

@then("the member is not an administrator")
def member_is_not_admin(member_created: dict) -> None:
    assert member_created["is_admin"] is False, member_created

@then("the member can list the tenant's gateways")
def member_lists(member_created: dict) -> None:
    response = member_created["browser"].get("/api/gateways")
    assert response.status_code == 200, response.text
    assert [g["slug"] for g in response.json()["gateways"]] == ["demo"], response.text

@then("the member cannot create a gateway")
def member_cannot_create(member_created: dict) -> None:
    response = member_created["browser"].post(
        "/api/gateways",
        json={"slug": "theirs", "name": "Theirs", "target_url": "http://up.test/mcp"},
    )
    assert response.status_code == 403, response.text

@then("the member cannot read the tenant's sign-in settings")
def member_cannot_read_settings(member_created: dict) -> None:
    assert member_created["browser"].get("/api/settings/sign-in").status_code == 403

@then("the tenant lists both accounts, with their roles")
def lists_members(owner_created: dict) -> None:
    response = owner_created["browser"].get("/api/operators")
    assert response.status_code == 200, response.text
    members = {m["email"]: m["role"] for m in response.json()["operators"]}
    assert members == {OWNER: "admin", MEMBER: "member"}, members

@then("the member cannot configure a gateway")
def member_cannot_configure(member_created: dict, gateway: dict) -> None:
    response = member_created["browser"].put(
        f"/api/gateways/{gateway['id']}/rate-limit", json={"requests_per_minute": 0}
    )
    assert response.status_code == 403, response.text
