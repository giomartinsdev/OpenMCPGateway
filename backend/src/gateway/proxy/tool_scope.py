"""Per-source tool visibility for `tools/list`.

The guard refuses a `tools/call` outside a source's list, but refusing is only
half the answer: a client told about a tool it cannot call retries forever. The
list has to be narrowed too, and the narrowing depends on *who* is asking — a
key minted for a bot and a person signed in through the IdP share one route.

`Visibility` cannot do it: it is installed once per proxy, at build time, and a
transform cannot see the request. A `Middleware` can, and FastMCP runs it inside
the request, so the identity the guard resolved is already on the scope.
"""

from typing import Any

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import types as mt
from mcp.types import Tool


class ToolScopeMiddleware(Middleware):
    """Hide the tools the caller's credential source may not call."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, list[Tool]],
    ) -> list[Tool]:
        tools = await call_next(context)
        allowed = self._allowed()
        if not allowed:
            return tools
        return [tool for tool in tools if tool.name in allowed]

    def _allowed(self) -> tuple[str, ...]:
        """The allowlist for this request's source, or empty for "all"."""
        scopes: dict[str, list[str]] = self._config.get("tool_scopes") or {}
        source = _source_of_request()
        if source is not None and scopes.get(source):
            return tuple(scopes[source])
        return tuple(self._config.get("tools") or [])


def _source_of_request() -> str | None:
    """The source the guard authenticated, or None outside an HTTP request."""
    try:
        identity = get_http_request().scope.get("omg.identity")
    except RuntimeError:
        return None
    return getattr(identity, "source", None)
