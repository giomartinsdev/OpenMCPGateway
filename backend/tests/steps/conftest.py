"""Operator steps shared by every feature.

pytest-bdd binds step definitions to the module that registered the scenarios, so
a step used by two feature files must be imported by both test modules. Defining
it twice would be a duplicate-step error, hence this single home.
"""

import pytest
from pytest_bdd import given, parsers

OPERATOR_EMAIL = "ops@example.dev"
OPERATOR_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def operator(container, runner) -> str:
    async def ensure() -> str:
        if await container.store.fetch_operator_by_email(OPERATOR_EMAIL):
            return OPERATOR_EMAIL
        await container.commands.register_operator(
            email=OPERATOR_EMAIL, password=OPERATOR_PASSWORD, is_admin=True
        )
        return OPERATOR_EMAIL

    return runner.run(ensure())


@given(
    parsers.parse('an operator "{email}" with password "{password}"'),
    target_fixture="operator",
)
def operator_step(container, runner, email: str, password: str) -> str:
    async def ensure() -> str:
        if await container.store.fetch_operator_by_email(email):
            return email
        await container.commands.register_operator(
            email=email, password=password, is_admin=True
        )
        return email

    return runner.run(ensure())
