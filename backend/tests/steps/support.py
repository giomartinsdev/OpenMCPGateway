"""Shared step state and a real backend MCP server for proxy tests.

The backend is a genuine FastMCP server, not a fake: the whole point of these
tests is that the gateway speaks MCP end-to-end, so a stub HTTP endpoint would
prove nothing about the proxy.
"""

import asyncio
import socket
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Context:
    """Mutable state passed between steps within one scenario."""

    container: Any = None
    server_url: str = ""
    gateway_id: str = ""
    slug: str = ""
    api_key: str = ""
    api_key_id: str = ""
    error: str = ""
    response: Any = None
    backend: Any = None
    backend_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BackendServer:
    """A real FastMCP server the gateway proxies to.

    Pass `require_token` to make it behave like the MCP servers in production
    (`rancher-mcp`, `db-mcp`): every request must carry that bearer. That is what
    turns "the gateway can forward an upstream credential" from a claim into a
    test — without it, a missing header still returns 200 and the bug ships.
    """

    def __init__(self, require_token: str | None = None) -> None:
        self.port = free_port()
        self.task: asyncio.Task[None] | None = None
        self.server = None
        self.require_token = require_token
        # Every Authorization header the upstream received, in order.
        self.seen: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def start(self) -> None:
        import uvicorn
        from fastmcp import FastMCP

        mcp = FastMCP("backend")

        @mcp.tool
        def echo(text: str) -> str:
            return text

        app = mcp.http_app(path="/mcp")
        if self.require_token:
            app = _RequireBearer(app, self.require_token, self.seen)
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(100):
            if self.server.started:
                return
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            await self.task
            self.task = None


class SecureCookieClient(httpx.Client):
    """An httpx client that keeps `Secure` cookies over plain http.

    The app sets `Secure` because it publishes an https base URL, which is
    correct in production and unusable in a test. httpx is right to drop such a
    cookie; the tests need the session, so they carry it by hand.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.jar: dict[str, str] = {}

    def build_request(self, method, url, **kwargs):
        request = super().build_request(method, url, **kwargs)
        if self.jar:
            request.headers["cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.jar.items()
            )
        return request

    def send(self, request, **kwargs):
        response = super().send(request, **kwargs)
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            self.jar[name.strip()] = rest.split(";", 1)[0].strip('"')
        return response


class _RequireBearer:
    """Reject anything without the expected bearer, before FastMCP sees it."""

    def __init__(self, app, token: str, seen: list[str]) -> None:
        self.app = app
        self.token = token
        self.seen = seen

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.datastructures import Headers
        from starlette.responses import JSONResponse

        header = Headers(scope=scope).get("authorization") or ""
        self.seen.append(header)
        if header != f"Bearer {self.token}":
            await JSONResponse({"error": "unauthorised"}, status_code=401)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
