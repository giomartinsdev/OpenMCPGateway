"""Steps for the JSON log contract Grafana depends on.

These run without the database: the logger is a unit, and a scenario that starts
Postgres to check a JSON line would be slower for no extra confidence.
"""

import io
import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.log import JsonLogger, configure

scenarios("../features/structured_logging.feature")


@pytest.fixture()
def output() -> io.StringIO:
    return io.StringIO()


@pytest.fixture()
def logger(output: io.StringIO) -> JsonLogger:
    # `configure` is idempotent per formatter, so a fresh stream needs a fresh
    # handler; replacing the formatter is the cheapest way to point at `output`.
    import logging

    root = logging.getLogger()
    handler = logging.StreamHandler(output)
    handler.setFormatter(_formatter())
    root.handlers[:] = [handler]
    root.setLevel("INFO")
    logger = JsonLogger("gateway.test")
    logger.unbind("gateway", "request_id")
    return logger


def _formatter():
    from gateway.log import JsonFormatter

    return JsonFormatter()


@when(parsers.parse('the application logs an event named "{event}"'))
def log_event(logger: JsonLogger, event: str) -> None:
    logger.info(event, gateway_id="gw_1")


@when(parsers.parse('the application logs inside a request for gateway "{slug}"'))
def log_in_context(logger: JsonLogger, slug: str) -> None:
    with logger.context(gateway=slug):
        logger.info("request.started", path="/g/x/mcp")
        logger.info("request.finished", status=200)


@when("the application logs an event carrying an unserialisable value")
def log_unserialisable(logger: JsonLogger) -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    logger.info("weird.value", payload=Opaque())


@then("every line is valid JSON")
def every_line_is_json(output: io.StringIO) -> None:
    lines = [line for line in output.getvalue().splitlines() if line.strip()]
    assert lines, "nothing was logged"
    for line in lines:
        json.loads(line)


@then(parsers.parse('the line has level "{level}" and event "{event}"'))
def line_has_level_and_event(output: io.StringIO, level: str, event: str) -> None:
    line = json.loads(output.getvalue().splitlines()[-1])
    assert line["level"] == level, line
    assert line["event"] == event, line
    assert "ts" in line and "logger" in line, line


@then(parsers.parse('both lines carry the gateway "{slug}"'))
def both_lines_carry_slug(output: io.StringIO, slug: str) -> None:
    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(lines) == 2, lines
    assert all(line["gateway"] == slug for line in lines), lines


@then("the field is gone after the request ends")
def field_is_gone(logger: JsonLogger, output: io.StringIO) -> None:
    logger.info("after.request")
    line = json.loads(output.getvalue().splitlines()[-1])
    assert "gateway" not in line, line


@then("the call returns normally")
def call_returns() -> None:
    # Reaching this step means `logger.info` did not raise; nothing to assert.
    assert True


@then(parsers.parse('the line still names the event "{event}"'))
def line_names_event(output: io.StringIO, event: str) -> None:
    line = json.loads(output.getvalue().splitlines()[-1])
    assert line["event"] == event, line
    assert line["payload"] == "<opaque>", line


@when(parsers.parse('the application logs an event carrying a field called "{field}"'))
def log_reserved_field(logger: JsonLogger, field: str) -> None:
    logger.info("database.migration", **{field: "readmodels_schema"})


@then(parsers.parse('the line has a "{field}" field with the value "{value}"'))
def line_has_field(output: io.StringIO, field: str, value: str) -> None:
    lines = [line for line in output.getvalue().splitlines() if line.strip()]
    assert lines, "the log line vanished instead of carrying the field"
    line = json.loads(lines[-1])
    assert line[field] == value, line


@given("the logger is configured")
def logger_configured(output: io.StringIO) -> None:
    configure("INFO", stream=output)
