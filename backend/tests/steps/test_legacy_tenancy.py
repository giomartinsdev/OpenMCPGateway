"""Steps for upgrading an installation that predates tenancy.

The upgrade is not a replay: the projection checkpoint says those events were
already projected, so the new `tenant_id` column arrives empty. What identifies
those rows is the *log* — an event written before the field existed has no
`tenant_id` key at all, while a new signup stores it as `null`. These steps write
that exact shape, because re-encoding through today's event class would put the
key back and prove nothing.
"""

import datetime

import httpx
import pytest
from pytest_bdd import given, scenarios, then, when
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gateway.application.container import Container, adopt_legacy_tenants
from gateway.domain.events import TENANT_OF_DEPLOYMENT, OperatorRegistered
from gateway.infrastructure.db.tables import operator_emails, rm_gateways
from gateway.infrastructure.security import hash_password
from tests.steps.support import SecureCookieClient

scenarios("../features/legacy_tenancy.feature")

LEGACY_EMAIL = "legacy@example.dev"
LEGACY_PASSWORD = "correct-horse-battery"
LEGACY_OPERATOR = "op_legacy00000001"
LEGACY_GATEWAY = "gw_legacy00000001"


@pytest.fixture()
def browser(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


@given("an account from before tenants existed", target_fixture="legacy")
def legacy_account(container: Container, runner) -> dict:
    """An account whose event and projection both predate the tenant column."""

    async def seed() -> dict:
        from gateway.infrastructure.db.tables import events, rm_operators

        payload = {
            "email": LEGACY_EMAIL,
            "password_hash": hash_password(LEGACY_PASSWORD),
            "is_admin": True,
            "role_ids": [],
        }
        assert "tenant_id" not in payload
        async with container.database.session() as session, session.begin():
            await session.execute(
                pg_insert(events).values(
                    event_id="ev_legacy00000001",
                    aggregate_id=LEGACY_OPERATOR,
                    sequence=1,
                    event_type=OperatorRegistered.EVENT,
                    payload=payload,
                    actor="legacy",
                    occurred_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(
                pg_insert(rm_operators).values(
                    id=LEGACY_OPERATOR,
                    email=LEGACY_EMAIL,
                    tenant_id=None,
                    role_ids=[],
                    is_admin=True,
                )
            )
            await session.execute(
                pg_insert(operator_emails).values(
                    email=LEGACY_EMAIL, aggregate_id=LEGACY_OPERATOR
                )
            )
        return {"operator_id": LEGACY_OPERATOR}

    return runner.run(seed())


@given("a gateway created before tenants existed")
def legacy_gateway(container: Container, runner) -> None:
    """The old row has no tenant column value; the schema default adopts it."""

    async def seed() -> None:
        from gateway.infrastructure.db.tables import events

        async with container.database.session() as session, session.begin():
            await session.execute(
                pg_insert(events).values(
                    event_id="ev_legacy00000002",
                    aggregate_id=LEGACY_GATEWAY,
                    sequence=1,
                    event_type="gateway.created",
                    payload={
                        "slug": "legacy-mcp",
                        "name": "Legacy",
                        "target_url": "http://up.test/mcp",
                    },
                    actor="legacy",
                    occurred_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(
                pg_insert(rm_gateways).values(
                    id=LEGACY_GATEWAY,
                    slug="legacy-mcp",
                    name="Legacy",
                    target_url="http://up.test/mcp",
                    # No tenant_id: the column's server default is what adopts it.
                    auth_mode="none",
                )
            )

    runner.run(seed())


@when("the container boots again")
def boots_again(container: Container, runner) -> None:
    """Boot is where the adoption runs, so this is where to look."""
    runner.run(adopt_legacy_tenants(container))


@when("a new signup registers", target_fixture="fresh")
def new_signup(container: Container, runner) -> str:
    async def seed() -> str:
        return await container.commands.register_operator(
            email="fresh@example.dev", password=LEGACY_PASSWORD, tenant_id=None
        )

    return runner.run(seed())


@when("that account signs in", target_fixture="signed_in")
def signs_in(browser: httpx.Client) -> dict:
    response = browser.post(
        "/api/auth/login",
        json={"email": LEGACY_EMAIL, "password": LEGACY_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


@then("that row is in the installation's tenant")
def row_in_installation(container: Container, runner) -> None:
    async def read() -> str | None:
        return await container.read_models.operator_tenant(LEGACY_OPERATOR)

    assert runner.run(read()) == TENANT_OF_DEPLOYMENT


@then("it can sign in without being asked to onboard")
def signs_in_without_onboarding(browser: httpx.Client, signed_in: dict) -> None:
    # The failure this pins: `tenant_id` left null by the upgrade, which turns
    # every account that has been running all along into a stuck signup.
    assert signed_in["tenant_id"] == TENANT_OF_DEPLOYMENT, signed_in
    response = browser.get("/api/gateways")
    assert response.status_code == 200, response.text


@then("it is listed for the installation's tenant")
def listed_for_installation(container: Container, runner) -> None:
    async def read() -> list[str]:
        rows = await container.read_models.list_gateways(TENANT_OF_DEPLOYMENT)
        return [row["slug"] for row in rows]

    assert runner.run(read()) == ["legacy-mcp"]


@then("a new signup is still without a tenant")
def new_signup_untenanted(container: Container, runner, fresh: str) -> None:
    """The backfill must not adopt accounts that deliberately have no tenant."""

    async def read() -> str | None:
        await adopt_legacy_tenants(container)
        return await container.read_models.operator_tenant(fresh)

    assert runner.run(read()) is None


@then("the installation's tenant has a join code")
def has_join_code(container: Container, runner) -> None:
    async def read() -> dict:
        row = await container.read_models.tenant(TENANT_OF_DEPLOYMENT)
        assert row is not None, "no tenant row was adopted"
        return dict(row)

    row = runner.run(read())
    assert len(row["join_code"]) == 8, row
