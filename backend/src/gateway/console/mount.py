"""The console MCP, mounted at `/mcp` next to the reverse proxy.

Same process, same container, one more ASGI branch: a gateway is a *proxied*
server, the console is the gateway's own read-only MCP. Keeping it out of
`/g/<slug>/` is what keeps the namespace honest — that path means "something the
tenant configured".

Auth is a bearer console token, minted from a session cookie at
`POST /api/mcp/token`. See `gateway.console.tokens` for why it is a second
credential rather than a reuse of the cookie.
"""

from typing import Any

from gateway.application.container import Container


def console_app(container: Container) -> Any:
    """The Starlette app serving `/mcp`, over the container's console server.

    The server itself lives on the container: the tools are the same ones a test
    calls directly, and building a second one here would give the process two
    verifiers and two tool registries to keep in step.
    """
    return container.console.http_app(path="/mcp", stateless_http=True)


__all__ = ["console_app"]
