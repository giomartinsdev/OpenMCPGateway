"""The allowlist must decide on a body it cannot read, and survive one it can.

`_blocked_tool` answers `None` for "this request names no tool". A body that
fails `json.loads` here used to produce that same `None`, so anything the guard
could not parse travelled to the upstream unchecked — with any body the upstream
accepts and this parse rejects, the allowlist was simply not applied.

`params` is attacker-controlled too: a list or a string used to raise inside the
middleware, which turned a malformed call into a 502 instead of a 403.
"""

from types import SimpleNamespace

import pytest

from gateway.proxy.guard import GuardMiddleware, _called_tool

ALLOWED = ["echo"]
SOURCE = "google"


def _blocked(body: bytes, allowed: list[str]) -> str | None:
    """What the guard decides for this body, per source.

    `_blocked_tool` reads the allowlist for the caller's source and nothing
    else, so a bare stand-in for the middleware keeps this free of a server.
    """
    guard = SimpleNamespace(config={"tools": allowed})
    return GuardMiddleware._blocked_tool(guard, body, SOURCE)


def test_a_named_tool_outside_the_allowlist_is_refused() -> None:
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"exec"}}'
    assert _blocked(body, ALLOWED) == "exec"


def test_a_named_tool_inside_the_allowlist_is_allowed() -> None:
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo"}}'
    assert _blocked(body, ALLOWED) is None


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"method":"tools/call","params":{"name":"exec"', id="truncated"),
        pytest.param(b"\xff\xfe not json", id="not-utf8"),
        pytest.param(b"GET / HTTP/1.1\r\n", id="not-json"),
    ],
)
def test_a_body_the_guard_cannot_parse_is_refused(body: bytes) -> None:
    """The answer is a refusal, and never `None` — that value means "cleared"."""
    assert _blocked(body, ALLOWED) is not None


def test_an_empty_allowlist_still_admits_everything() -> None:
    """The fix must not turn "no allowlist" into "deny all"."""
    assert _blocked(b"not json at all", []) is None


def test_an_empty_body_is_not_a_refusal() -> None:
    """SSE polls and the disconnect path carry no body; they name no tool."""
    assert _blocked(b"", ALLOWED) is None


def test_a_tool_scope_narrows_the_gateway_list() -> None:
    """A per-source scope replaces `tools`, and an unreadable body must not skip
    the narrowed list either."""
    guard = SimpleNamespace(
        config={"tools": ["echo"], "tool_scopes": {SOURCE: ["read"]}}
    )

    def blocked(body: bytes) -> str | None:
        return GuardMiddleware._blocked_tool(guard, body, SOURCE)

    assert blocked(b'{"method":"tools/call","params":{"name":"echo"}}') == "echo"
    assert blocked(b'{"method":"tools/call","par') is not None
    assert blocked(b'{"method":"tools/call","params":{"name":"read"}}') is None


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"method":"tools/call","params":[1,2]}', id="list"),
        pytest.param(b'{"method":"tools/call","params":"exec"}', id="string"),
        pytest.param(b'{"method":"tools/call","params":null}', id="null"),
    ],
)
def test_non_dict_params_does_not_raise(body: bytes) -> None:
    """A malformed `params` is a call naming no tool we can check, not a crash.

    Raising here would answer 502 with the caller choosing the moment, and a
    502 in a traffic row reads as "upstream broke" when the caller sent it.
    """
    assert _blocked(body, ALLOWED) is None
    assert _called_tool(body) is None


def test_a_batch_is_checked_message_by_message() -> None:
    """One HTTP body may carry several calls; a refused one refuses the batch."""
    body = (
        b'[{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo"}},'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"exec"}}]'
    )
    assert _blocked(body, ALLOWED) == "exec"
