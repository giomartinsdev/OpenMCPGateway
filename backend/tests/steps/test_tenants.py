"""Steps for creating or joining a tenant.

Every scenario uses a second cookie jar for the second person, because the point
is what a *different* account sees — and most of these scenarios are about
isolation, which a shared jar would fake.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient, create_tenant, register

scenarios("../features/tenants.feature")

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def clients(live_server: str) -> dict[str, httpx.Client]:
    """One cookie jar per person, made on first use.

    A scenario that says "Bob does X" must talk to the server as Bob, and the
    only way to be Bob is a separate jar: reusing the browser would silently
    replace Alice's session and turn an isolation test into a shared-account
    test that passes for the wrong reason.
    """
    jars: dict[str, httpx.Client] = {}
    try:
        yield jars
    finally:
        for client in jars.values():
            client.close()


@pytest.fixture()
def ctx() -> dict:
    return {}


def _browser(clients: dict[str, httpx.Client], live_server: str,
             email: str) -> httpx.Client:
    if email not in clients:
        clients[email] = SecureCookieClient(
            base_url=live_server, follow_redirects=False
        )
    return clients[email]


def _join(account: dict, code: str) -> httpx.Response:
    return account["browser"].post("/api/tenants/join", json={"code": code})


def _owned(clients: dict[str, httpx.Client], live_server: str, ctx: dict,
           email: str, tenant: str) -> dict:
    browser = _browser(clients, live_server, email)
    # Already registered in this scenario ("someone registered as Alice" followed
    # by "Alice created..."), so only the tenant is new.
    account = ctx[email] = ctx.get(email) or register(browser, email)
    account["tenant"] = create_tenant(browser, tenant)
    return account


def _gateway(account: dict, slug: str) -> dict:
    response = account["browser"].post(
        "/api/gateways",
        json={"slug": slug, "name": slug, "target_url": "http://up.test/mcp"},
    )
    assert response.status_code == 201, response.text
    account["gateway_id"] = response.json()["id"]
    account["slug"] = slug
    return account


# -- given -----------------------------------------------------------------


@given(parsers.parse('someone registered as "{email}"'), target_fixture="account")
def someone_registered(clients: dict[str, httpx.Client], live_server: str,
                       ctx: dict, email: str) -> dict:
    account = register(_browser(clients, live_server, email), email)
    ctx[email] = account
    return account


@given(parsers.parse('the founder created the tenant "{name}"'),
       target_fixture="tenant")
def founder_created(ctx: dict, name: str) -> dict:
    founder = ctx["founder@example.dev"]
    founder["tenant"] = create_tenant(founder["browser"], name)
    return founder["tenant"]


@given(parsers.parse('someone registered as "{email}" joined the tenant "{name}"'),
       target_fixture="teammate")
def teammate_joined(clients: dict[str, httpx.Client], live_server: str,
                    ctx: dict, email: str, name: str) -> dict:
    del name
    account = register(_browser(clients, live_server, email), email)
    response = _join(account, ctx["founder@example.dev"]["tenant"]["join_code"])
    assert response.status_code == 200, response.text
    account.update(response.json())
    ctx[email] = account
    return account


@given(parsers.parse('Alice created the tenant "{name}" with the gateway "{slug}"'),
       target_fixture="alice")
def alice_with_gateway(clients: dict[str, httpx.Client], live_server: str,
                       ctx: dict, name: str, slug: str) -> dict:
    return _gateway(
        _owned(clients, live_server, ctx, "alice@example.dev", name), slug
    )


@given(parsers.parse('Bob created the tenant "{name}" with the gateway "{slug}"'),
       target_fixture="bob")
def bob_with_gateway(clients: dict[str, httpx.Client], live_server: str,
                     ctx: dict, name: str, slug: str) -> dict:
    return _gateway(
        _owned(clients, live_server, ctx, "bob@example.dev", name), slug
    )


@given(parsers.parse('Alice created the tenant "{name}"'), target_fixture="alice")
def alice_with_tenant(clients: dict[str, httpx.Client], live_server: str,
                      ctx: dict, name: str) -> dict:
    return _owned(clients, live_server, ctx, "alice@example.dev", name)


@given(parsers.parse('Bob created the tenant "{name}"'), target_fixture="bob")
def bob_with_tenant(clients: dict[str, httpx.Client], live_server: str,
                    ctx: dict, name: str) -> dict:
    return _owned(clients, live_server, ctx, "bob@example.dev", name)


# -- when ------------------------------------------------------------------


@when(parsers.parse('someone registers as "{email}"'), target_fixture="account")
def someone_registers(clients: dict[str, httpx.Client], live_server: str,
                      email: str) -> dict:
    return register(_browser(clients, live_server, email), email)


@when(parsers.parse('the founder creates the tenant "{name}"'),
      target_fixture="tenant")
def founder_creates(ctx: dict, name: str) -> dict:
    founder = ctx["founder@example.dev"]
    founder["tenant"] = create_tenant(founder["browser"], name)
    return founder["tenant"]


@when(parsers.parse('someone registered as "{email}" joins with the code'),
      target_fixture="joiner")
def joins_with_founder_code(clients: dict[str, httpx.Client],
                           live_server: str, ctx: dict, email: str) -> dict:
    account = register(_browser(clients, live_server, email), email)
    account["response"] = _join(
        account, ctx["founder@example.dev"]["tenant"]["join_code"]
    )
    if account["response"].status_code == 200:
        account.update(account["response"].json())
    return account


@when(parsers.parse('someone registered as "{email}" joins with the code "{code}"'),
      target_fixture="joiner")
def joins_with_bad_code(clients: dict[str, httpx.Client], live_server: str,
                        email: str, code: str) -> dict:
    account = register(_browser(clients, live_server, email), email)
    account["response"] = _join(account, code)
    return account


@when(parsers.parse('the teammate joins the tenant "{name}" again'),
      target_fixture="joiner")
def teammate_joins_again(ctx: dict, teammate: dict) -> dict:
    teammate["response"] = _join(
        teammate, ctx["founder@example.dev"]["tenant"]["join_code"]
    )
    return teammate


@when("the lone account creates a gateway", target_fixture="attempt")
def lone_creates_gateway(account: dict) -> httpx.Response:
    return account["browser"].post(
        "/api/gateways",
        json={"slug": "lonely", "name": "Lonely", "target_url": "http://up.test/mcp"},
    )


@when("Alice lists her gateways", target_fixture="listing")
def alice_lists(alice: dict) -> list[str]:
    response = alice["browser"].get("/api/gateways")
    assert response.status_code == 200, response.text
    return [gw["slug"] for gw in response.json()["gateways"]]


@when("Alice lists her members", target_fixture="members")
def alice_lists_members(alice: dict) -> list[str]:
    response = alice["browser"].get("/api/operators")
    assert response.status_code == 200, response.text
    return sorted(m["email"] for m in response.json()["operators"])


@when(parsers.parse('Alice defines the role "{name}" with "{permissions}"'),
      target_fixture="defined")
def alice_defines(alice: dict, name: str, permissions: str) -> dict:
    response = alice["browser"].post(
        "/api/roles",
        json={
            "name": name,
            "permissions": [p.strip() for p in permissions.split(",") if p.strip()],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@when("Bob reads Alice's gateway by id", target_fixture="attempt")
def bob_reads_alice(alice: dict, bob: dict) -> httpx.Response:
    return bob["browser"].get(f"/api/gateways/{alice['gateway_id']}")


# -- then ------------------------------------------------------------------


@then("the new account has no tenant")
def no_tenant(account: dict) -> None:
    assert account["tenant_id"] is None, account


@then("it is offered the choice of creating or joining a tenant")
def offered_choice(account: dict) -> None:
    identity = account["browser"].get("/api/auth/me")
    assert identity.status_code == 200, identity.text
    body = identity.json()
    assert body["tenant_id"] is None, body
    assert body["permissions"] == [], body
    assert body["onboarding"] == ["create", "join"], body


@then(parsers.parse('the founder owns the tenant "{name}"'))
def founder_owns(tenant: dict, name: str) -> None:
    assert tenant["name"] == name, tenant


@then(parsers.parse('the founder holds the "{role_id}" role'))
def founder_role(tenant: dict, role_id: str) -> None:
    assert tenant["role_ids"] == [role_id], tenant


@then("the tenant has a join code the founder can share")
def join_code(tenant: dict) -> None:
    assert len(tenant["join_code"]) >= 8, tenant
    # Stable across reads: a code that changes is a code nobody can use.
    again = tenant["browser"].get("/api/tenants/current")
    assert again.status_code == 200, again.text
    assert again.json()["join_code"] == tenant["join_code"], again.text


@then(parsers.parse('the teammate belongs to the tenant "{name}"'))
def teammate_belongs(joiner: dict, name: str) -> None:
    assert joiner["response"].status_code == 200, joiner["response"].text
    assert joiner["tenant_name"] == name, joiner


@then(parsers.parse('the teammate holds the "{role_id}" role'))
def teammate_role(joiner: dict, role_id: str) -> None:
    me = joiner["browser"].get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["role_ids"] == [role_id], me.text


@then("the join is refused")
def join_refused(joiner: dict) -> None:
    response = joiner["response"]
    assert response.status_code in (403, 422), response.text
    # One message for a wrong code and for already belonging: which one it was
    # is not the stranger's business.
    assert response.json()["detail"], response.text


@then(parsers.parse('the request is refused with "{fragment}"'))
def refused_with(attempt: httpx.Response, fragment: str) -> None:
    assert attempt.status_code == 403, attempt.text
    assert fragment in attempt.json()["detail"], attempt.text


@then(parsers.parse('Alice sees only "{slug}"'))
def alice_sees(listing: list[str], slug: str) -> None:
    assert listing == [slug], listing


@then(parsers.parse('Bob sees only "{slug}"'))
def bob_sees(bob: dict, slug: str) -> None:
    response = bob["browser"].get("/api/gateways")
    assert response.status_code == 200, response.text
    assert [gw["slug"] for gw in response.json()["gateways"]] == [slug], response.text


@then("Alice sees only herself")
def alice_sees_herself(members: list[str]) -> None:
    assert members == ["alice@example.dev"], members


@then(parsers.parse("Bob's roles do not include \"{name}\""))
def bob_has_no_role(bob: dict, name: str) -> None:
    response = bob["browser"].get("/api/roles")
    assert response.status_code == 200, response.text
    names = [role["name"] for role in response.json()["roles"]]
    assert name not in names, names


@then("the gateway is not found")
def gateway_not_found(attempt: httpx.Response) -> None:
    assert attempt.status_code == 404, attempt.text
