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

        @mcp.tool
        def delete_repo(repo: str) -> str:
            """A second tool, so a per-source allowlist has something to hide."""
            return repo

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


#: The MCP handshake, as a dict: every proxy helper below starts from it.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}

def call_mcp(
    base_url: str,
    slug: str,
    key: str | None = None,
    tool: str | None = None,
    *,
    arguments: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> httpx.Response:
    """A full MCP exchange, so "200" means the proxy actually worked.

    MCP needs initialize, then `notifications/initialized`, then the call; a bare
    POST answers "Missing session ID" and would prove nothing about auth. Returns
    the *initialize* response, because that is the one whose headers carry the
    request id and whose status carries the refusal.
    """
    headers = {"Accept": "application/json, text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if request_id:
        headers["X-Request-Id"] = request_id
    with httpx.Client(timeout=20) as client:
        started = client.post(
            f"{base_url}/g/{slug}/mcp", json=INITIALIZE, headers=headers
        )
        session = started.headers.get("mcp-session-id", "")
        if session:
            client.post(
                f"{base_url}/g/{slug}/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={**headers, "mcp-session-id": session},
            )
            payload = (
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments or {}},
                }
                if tool
                else {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            )
            client.post(
                f"{base_url}/g/{slug}/mcp",
                json=payload,
                headers={**headers, "mcp-session-id": session},
            )
        return started

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


PASSWORD = "correct-horse-battery"

def register(browser: httpx.Client, email: str) -> dict[str, Any]:
    """Create an account and stay signed in. No tenant: signup grants identity only."""
    response = browser.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return {"browser": browser, "email": email, **response.json()}

def create_tenant(browser: httpx.Client, name: str) -> dict[str, Any]:
    """Create a tenant as the signed-in account, which becomes its owner.

    Re-reads `/api/auth/me` afterwards, like the panel does: the tenant response
    is what changed, and the roles that came with it are only visible through the
    identity endpoint.
    """
    response = browser.post("/api/tenants", json={"name": name})
    assert response.status_code == 201, response.text
    me = browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    identity = me.json()
    return {
        **response.json(),
        "role_ids": identity["role_ids"],
        "is_admin": identity["is_admin"],
        "browser": browser,
    }

def owner_account(browser: httpx.Client, email: str,
                  tenant: str = "Acme") -> dict[str, Any]:
    """The usual test setup: an account that owns a tenant, able to do anything.

    Almost every scenario wants a working panel, not the onboarding screen, so
    this names the two commands as one helper instead of repeating them.
    """
    account = register(browser, email)
    account.update(create_tenant(browser, tenant))
    account.update(
        {
            "role_ids": ["role_owner"],
            "is_admin": True,
        }
    )
    return account

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
