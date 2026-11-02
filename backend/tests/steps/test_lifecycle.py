"""Steps for gateway lifecycle and config-file scenarios.

Steps are synchronous by necessity (pytest-bdd invokes them directly); async
work is submitted to the shared test loop through `runner`.

`operator` is a real fixture rather than a step-set `target_fixture`: a
Background step would otherwise leave later scenarios without the value.
"""

from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.configfile import ConfigFileError, apply_document, parse_document
from gateway.domain.events import TENANT_OF_DEPLOYMENT
from gateway.domain.model import DomainError
from tests.steps.support import Context

scenarios("../features/gateway_lifecycle.feature")
scenarios("../features/config_file.feature")

OPERATOR_EMAIL = "ops@example.dev"
OPERATOR_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def ctx() -> Context:
    return Context()


# -- given -----------------------------------------------------------------


@given(
    parsers.parse('a gateway "{slug}" pointing at "{target}"'),
    target_fixture="gateway_row",
)
def gateway_row(container, runner, operator: str, slug: str, target: str) -> dict:
    async def create() -> dict:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url=target, actor=operator
        )
        await container.refresh_router()
        return await container.read_models.get_gateway(gateway_id, TENANT_OF_DEPLOYMENT)

    return runner.run(create())


@given(parsers.parse('a YAML document creating "{slug}"'), target_fixture="document")
def document_creating(slug: str):
    return parse_document(
        f"""
gateways:
  - slug: {slug}
    name: {slug.title()}
    target_url: http://backend.test/mcp
"""
    )


@given("a YAML document:", target_fixture="document")
def document_from_text(datatable=None, docstring=None, **kwargs):
    """Accept the step's docstring as the YAML body.

    pytest-bdd passes a multi-line step body positionally, so the signature has
    to tolerate both a docstring and a datatable depending on how the feature is
    written.
    """
    text = docstring or datatable
    if text is None:
        for value in kwargs.values():
            if isinstance(value, str):
                text = value
                break
    assert isinstance(text, str), "expected a YAML docstring on the step"
    return parse_document(text)


@given(
    parsers.parse('a YAML document referencing "{variable}"'),
    target_fixture="parse_error",
)
def document_with_variable(variable: str) -> str:
    text = f"""
gateways:
  - slug: demo
    name: Demo
    target_url: http://backend.test/mcp
    description: {variable}
"""
    try:
        parse_document(text)
    except ConfigFileError as exc:
        return str(exc)
    return ""


@given("a YAML document with oauth but no client_id", target_fixture="document")
def document_oauth_incomplete():
    return parse_document(
        """
gateways:
  - slug: demo
    name: Demo
    target_url: http://backend.test/mcp
    auth:
      mode: oauth
      idp: github
      client_secret: shhh
"""
    )


# -- when ------------------------------------------------------------------


@when(
    parsers.parse('the operator registers a gateway "{slug}" pointing at "{target}"'),
    target_fixture="gateway_id",
)
def register_gateway(container, runner, operator: str, slug: str, target: str) -> str:
    async def create() -> str:
        gateway_id = await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name=slug.title(), target_url=target, actor=operator
        )
        await container.refresh_router()
        return gateway_id

    return runner.run(create())


@when(
    parsers.parse('the operator registers another gateway with slug "{slug}"'),
    target_fixture="error",
)
def register_duplicate(container, runner, operator: str, slug: str) -> str:
    async def attempt() -> str:
        try:
            await container.commands.create_gateway(
                tenant_id=TENANT_OF_DEPLOYMENT,
                slug=slug, name="Again", target_url="http://backend.test/mcp",
                actor=operator,
            )
        except DomainError as exc:
            return str(exc)
        return ""

    return runner.run(attempt())


@when(
    parsers.parse(
        'the operator attempts a gateway with slug "{slug}" at "{target}"'
    ),
    target_fixture="error",
)
def register_invalid(container, runner, slug: str, target: str) -> str:
    async def attempt() -> str:
        try:
            await container.commands.create_gateway(
                tenant_id=TENANT_OF_DEPLOYMENT,
                slug=slug, name=slug, target_url=target, actor="test"
            )
        except DomainError as exc:
            return str(exc)
        return ""

    return runner.run(attempt())


@when(parsers.parse('the operator deletes gateway "{slug}"'))
def delete_gateway(container, runner, operator: str, slug: str) -> None:
    async def remove() -> None:
        row = await container.read_models.get_by_slug(slug)
        await container.commands.delete_gateway(row["id"], actor=operator)
        await container.refresh_router()

    runner.run(remove())


@when("the file is applied", target_fixture="apply_result")
def apply_file(container, runner, operator: str, document) -> dict:
    return runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )


@when("the document is applied", target_fixture="apply_result")
def apply_first(container, runner, operator: str, document) -> dict:
    return runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )


@when("the same document is applied again", target_fixture="reapply_result")
def apply_again(container, runner, operator: str, document) -> dict:
    return runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )


@when(
    parsers.parse('a document without "{slug}" is applied'),
    target_fixture="apply_result",
)
def apply_without(container, runner, operator: str) -> dict:
    document = parse_document(
        """
gateways:
  - slug: other
    name: Other
    target_url: http://backend.test/mcp
"""
    )
    return runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )


@when(
    parsers.parse('a document without "{slug}" is applied with prune enabled'),
    target_fixture="apply_result",
)
def apply_with_prune(container, runner, operator: str) -> dict:
    document = parse_document(
        """
prune: true
gateways:
  - slug: other
    name: Other
    target_url: http://backend.test/mcp
"""
    )
    return runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )


# -- then ------------------------------------------------------------------


@then(parsers.parse('gateway "{slug}" is listed'))
def gateway_listed(container, runner, slug: str) -> None:
    async def check() -> list[str]:
        gateways = await container.read_models.list_gateways(TENANT_OF_DEPLOYMENT)
        return [gw["slug"] for gw in gateways]

    names = runner.run(check())
    assert slug in names, f"{slug} not in {names}"


@then(parsers.parse('its public endpoint is "{endpoint}"'))
def endpoint_is(container, runner, gateway_id: str, endpoint: str) -> None:
    async def check() -> str:
        row = await container.read_models.get_gateway(gateway_id, TENANT_OF_DEPLOYMENT)
        return f"{container.settings.base_url}/g/{row['slug']}/mcp"

    assert runner.run(check()) == endpoint


@then("the command is rejected because the slug is taken")
def slug_taken(error: str) -> None:
    assert "already taken" in error


@then("the command is rejected because the slug is invalid")
def slug_invalid(error: str) -> None:
    assert "slug must be" in error


@then("the command is rejected because the target is not an http URL")
def target_invalid(error: str) -> None:
    assert "http(s) URL" in error


@then(parsers.parse('gateway "{slug}" is not listed'))
def gateway_not_listed(container, runner, slug: str) -> None:
    async def check() -> list[str]:
        gateways = await container.read_models.list_gateways(TENANT_OF_DEPLOYMENT)
        return [gw["slug"] for gw in gateways]

    assert slug not in runner.run(check())


@then(parsers.parse('the slug "{slug}" can be reused'))
def slug_reusable(container, runner, operator: str, slug: str) -> None:
    async def create() -> str:
        return await container.commands.create_gateway(
            tenant_id=TENANT_OF_DEPLOYMENT,
            slug=slug, name="Reused", target_url="http://backend.test/mcp",
            actor=operator,
        )

    assert runner.run(create())


@then(parsers.parse('gateway "{slug}" exists with limit {limit:d}'))
def exists_with_limit(container, runner, slug: str, limit: int) -> None:
    async def check() -> dict | None:
        return await container.read_models.get_by_slug(slug)

    row = runner.run(check())
    assert row is not None
    assert row["requests_per_minute"] == limit


@then("nothing changed")
def nothing_changed(reapply_result: dict) -> None:
    assert reapply_result["created"] == []
    assert reapply_result["updated"] == []
    assert reapply_result["unchanged"]


@then(parsers.parse('gateway "{slug}" still exists'))
def still_exists(container, runner, slug: str) -> None:
    async def check() -> Any:
        return await container.read_models.get_by_slug(slug)

    assert runner.run(check()) is not None


@then(parsers.parse('gateway "{slug}" is gone'))
def is_gone(container, runner, slug: str) -> None:
    async def check() -> Any:
        return await container.read_models.get_by_slug(slug)

    assert runner.run(check()) is None


@then("parsing fails with a clear message")
def parsing_failed(parse_error: str) -> None:
    assert "NOT_SET_ANYWHERE" in parse_error
    assert "not set" in parse_error


@then("applying fails because the gateway is incomplete")
def apply_failed(container, runner, operator: str, document) -> None:
    result = runner.run(
        apply_document(
            document, container.commands, container.read_models,
            tenant_id=TENANT_OF_DEPLOYMENT, actor=operator,
        )
    )
    assert "client_id" in " ".join(result["errors"])


@given(parsers.parse('the environment "{name}" is set to "{value}"'))
def set_environment(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)


@then(parsers.parse('the gateway "{slug}" has an upstream token'))
def gateway_has_upstream_token(container, runner, slug: str) -> None:
    async def check() -> None:
        gateway = await container.read_models.get_by_slug(slug)
        assert gateway is not None, f"gateway '{slug}' was not created"
        # Presence only: the value lives in the write model, never the read model.
        credentials = await container.router._credentials(gateway["id"])
        assert credentials.get("upstream_token") == "up-secret", credentials

    runner.run(check())
