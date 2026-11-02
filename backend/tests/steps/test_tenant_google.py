"""Steps for signing in with Google and landing in a tenant.

The scenarios here are about *where a verified address ends up*, which is the
half of the flow `operator_login.feature` does not cover: there, the provider's
verification is what is being tested; here, what happens to it afterwards.

`TENANT_OF_DEPLOYMENT` is the installation's own tenant — the one the bootstrap
admin owns and the allowlist joins. A stranger gets an identity and no tenant.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.application.container import Container
from gateway.domain.events import TENANT_OF_DEPLOYMENT
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
def browser(live_server: str) -> httpx.Client:
    client = httpx.Client(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


@given("the installation has an administrator")
def installation_admin(container: Container, runner) -> None:
    """The owner of the installation's tenant, created the way boot creates it.

    Written through the commands rather than the API on purpose: the API path
    would make a *new* tenant, and then the allowlist would have nowhere
    coherent to send the member.
    """
    async def seed() -> None:
        await container.commands.ensure_tenant(
            tenant_id=TENANT_OF_DEPLOYMENT, name="Default"
        )
        await container.commands.register_operator(
            email="owner@example.dev",
            password=PASSWORD,
            is_admin=True,
            tenant_id=TENANT_OF_DEPLOYMENT,
        )

    runner.run(seed())


@given(parsers.parse('the provider will vouch for "{email}"'))
def vouches_for(google: FakeGoogle, email: str) -> None:
    google.email = email


@when("the member starts a Google login", target_fixture="completed")
def member_signs_in(browser: httpx.Client) -> list[httpx.Response]:
    return _walk(browser, browser.get("/api/auth/google/start", follow_redirects=False))


def _signed_in(completed: list[httpx.Response]) -> None:
    locations = [r.headers.get("location") for r in completed]
    assert completed[-1].status_code == 303, (locations, completed[-1].text[:200])
    assert completed[-1].headers.get("location") == "/#/gateways", locations


def _identity(container: Container, runner) -> dict:
    async def read() -> dict:
        rows = await container.read_models.list_operators(TENANT_OF_DEPLOYMENT)
        return {row["email"]: row for row in rows}

    return runner.run(read())


@then("the member is signed in")
def member_signed_in(completed: list[httpx.Response]) -> None:
    _signed_in(completed)


@then("the member joins the installation's tenant")
def member_joins_tenant(container: Container, runner) -> None:
    members = _identity(container, runner)
    assert "member@example.dev" in members, members


@then("the member is not an administrator")
def member_is_not_admin(container: Container, runner) -> None:
    members = _identity(container, runner)
    assert members["member@example.dev"]["role_ids"] == ("role_viewer",), members
    assert members["owner@example.dev"]["role_ids"] == ("role_owner",), members


@then("the member has no tenant")
def member_has_no_tenant(container: Container, runner) -> None:
    async def tenant_of() -> str | None:
        operator_id = await container.store.fetch_operator_by_email(
            "stranger@elsewhere.test"
        )
        assert operator_id, "no account was created"
        return await container.read_models.operator_tenant(operator_id)

    assert runner.run(tenant_of()) is None
