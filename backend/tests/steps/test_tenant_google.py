"""Steps for joining a tenant by signing in with Google.

The scenarios here are about *who may in*, which is the half of the sign-in flow
`operator_login.feature` does not cover: there, the provider's verification is
what is being tested; here, what happens to the verified address afterwards.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.application.container import Container
from tests.steps.fake_google import FakeGoogle
from tests.steps.test_operator_login import _walk

scenarios("../features/tenant_google.feature")

CLIENT_ID = "omg-tenant-client.apps.googleusercontent.com"
CLIENT_SECRET = "omg-tenant-client-secret"
ALLOWED = ("member@example.dev",)
PASSWORD = "correct-horse-battery"

@pytest.fixture()
def google(runner) -> FakeGoogle:
    provider = FakeGoogle(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    runner.run(provider.start())
    try:
        yield provider
    finally:
        runner.run(provider.stop())

@pytest.fixture()
def container(runner, google, postgres) -> Container:
    from gateway.config import Settings

    settings = Settings(
        database_url=postgres,
        session_secret="test-secret-key-at-least-32-bytes-long!!",
        base_url="https://gateway.test",
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        google_discovery_url=f"{google.issuer}/.well-known/openid-configuration",
        operator_allowlist=ALLOWED,
    )
    instance = runner.run(Container.create(settings))
    try:
        yield instance
    finally:
        runner.run(instance.close())

@pytest.fixture()
def owner(live_server: str) -> httpx.Client:
    client = httpx.Client(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@pytest.fixture()
def browser(live_server: str) -> httpx.Client:
    client = httpx.Client(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@given("the installation has an administrator")
def installation_admin(owner: httpx.Client) -> None:
    response = owner.post(
        "/api/auth/register",
        json={"email": "owner@example.dev", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text

@given(parsers.parse('the provider will vouch for "{email}"'))
def vouches_for(google: FakeGoogle, email: str) -> None:
    google.email = email

@when("the member starts a Google login", target_fixture="completed")
def member_signs_in(browser: httpx.Client) -> list[httpx.Response]:
    return _walk(browser, browser.get("/api/auth/google/start", follow_redirects=False))

@then("the member is signed in")
def member_signed_in(completed: list[httpx.Response]) -> None:
    locations = [r.headers.get("location") for r in completed]
    assert completed[-1].status_code == 303, (locations, completed[-1].text[:200])
    assert completed[-1].headers.get("location") == "/#/gateways", locations

@then("the member joins the installation's tenant")
def member_joins_tenant(container: Container, runner) -> None:
    members = {m["email"]: m["role"] for m in runner.run(_members(container))}
    assert members["member@example.dev"] == "member", members

@then("the member is not an administrator")
def member_is_not_admin(container: Container, runner) -> None:
    members = {m["email"]: m["role"] for m in runner.run(_members(container))}
    assert members["owner@example.dev"] == "admin", members

@then("the login is refused with a reason")
def refused(completed: list[httpx.Response]) -> None:
    location = completed[-1].headers.get("location", "")
    assert completed[-1].status_code == 303, location
    assert location.startswith("/#/login?error="), location
    assert "stranger%40elsewhere.test" in location, location

async def _members(container: Container) -> list[dict]:
    return await container.store.list_operators()
