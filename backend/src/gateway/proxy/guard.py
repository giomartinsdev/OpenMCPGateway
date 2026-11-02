"""The ASGI middleware in front of every gateway mount.

It distinguishes 401 (bad/missing credential) from 403 (known caller, not
allowed) and 429 (over limit), and records every request. FastMCP still enforces
bearer auth underneath — defence in depth, not the only check.
"""

import json
import time
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.domain.events import AuthMode
from gateway.log import log
from gateway.observability import RequestRecorder
from gateway.proxy.identity import GatewayAuth, Identity
from gateway.proxy.rate_limit import RateLimiter

_RESOURCE_METADATA = "/.well-known/oauth-protected-resource"


class GuardMiddleware:
    """Auth + authorisation + rate limit + observation for one gateway mount."""

    def __init__(self, app: ASGIApp, *, config: dict[str, Any],
                 auth: GatewayAuth, limiter: RateLimiter,
                 recorder: RequestRecorder, base_url: str) -> None:
        self.app = app
        self.config = config
        self.auth = auth
        self.limiter = limiter
        self.recorder = recorder
        self.base_url = base_url.rstrip("/")
        self.prefix = f"/g/{config['slug']}"
        self.inner_path = "/mcp"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        started = time.perf_counter()
        identity: Identity | None = None
        response: JSONResponse | None = None
        outcome = "allowed"
        status = 200

        try:
            identity = await self.auth.resolve(_bearer(headers))
            if identity is None:
                outcome, status = "unauthenticated", 401
                response = self._unauthorized()
            elif not self._authorized(identity.subject):
                outcome, status = "forbidden", 403
                response = JSONResponse(
                    {
                        "error": "forbidden",
                        "message": f"'{identity.subject}' is not allowed on "
                                   f"gateway '{self.config['slug']}'",
                    },
                    status_code=403,
                )
            else:
                # Downstream code may read this; nothing authenticates off it.
                scope["omg.identity"] = identity
                body = await _read_body(receive)
                receive = _replay(body, receive)
                # Rate limiting counts MCP *requests* (JSON-RPC messages that
                # carry an id), not HTTP POSTs: one MCP call is a handshake plus
                # a call on the wire, and charging for the transport would make
                # a limit of N mean fewer than N calls to the user.
                if _is_jsonrpc_request(body) and not await self.limiter.allow(
                    self.config["id"],
                    identity.subject,
                    int(self.config.get("requests_per_minute") or 0),
                ):
                    outcome, status = "rate_limited", 429
                    response = JSONResponse(
                        {"error": "rate_limited", "message": "too many requests"},
                        status_code=429,
                        headers={"Retry-After": "60"},
                    )
                else:
                    status = await _run_capturing_status(
                        self.app, self._rewrite(scope), receive, send
                    )
        except Exception:
            outcome = "error"
            status = 502
            raise
        finally:
            log.info(
                "proxy.request",
                gateway=self.config["slug"],
                subject=identity.subject if identity else "anonymous",
                outcome=outcome,
                status=status,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ip=_client_ip(scope, headers),
                method=scope.get("method", "POST"),
            )
            await self.recorder.record(
                gateway_id=self.config["id"],
                subject=identity.subject if identity else "anonymous",
                outcome=outcome,
                status=status,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ip=_client_ip(scope, headers),
                tool=None,
                method=scope.get("method", "POST"),
            )

        if response is not None:
            await response(scope, receive, send)

    def _rewrite(self, scope: Scope) -> Scope:
        """Map the public path onto the wrapped app's internal path.

        The proxy app serves `/mcp`, but the gateway is published at
        `/g/<slug>/mcp`. Without this rewrite the inner router sees the prefixed
        path and answers 404 — which is exactly what a client would read as
        "gateway misconfigured".
        """
        rewritten = dict(scope)
        rewritten["root_path"] = scope.get("root_path", "") + self.prefix
        rewritten["path"] = self.inner_path
        return rewritten

    def _authorized(self, subject: str) -> bool:
        allowed: list[str] = self.config.get("allowed_subjects") or []
        return not allowed or subject in allowed or "*" in allowed

    def _unauthorized(self) -> JSONResponse:
        if self.auth.mode is AuthMode.OAUTH:
            challenge = (
                f'Bearer resource_metadata="{self.base_url}'
                f'{_RESOURCE_METADATA}/g/{self.config["slug"]}/mcp"'
            )
            return JSONResponse(
                {"error": "unauthorized"}, status_code=401,
                headers={"WWW-Authenticate": challenge},
            )
        return JSONResponse(
            {"error": "unauthorized", "message": "missing or invalid API key"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="openmcp-gateway"'},
        )


async def _read_body(receive: Receive) -> bytes:
    """Consume and buffer the request body so it can be inspected and replayed.

    `receive` is a one-shot stream: reading it for rate limiting would leave the
    downstream app with an empty body, so the bytes are captured here and handed
    back through `_replay`.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    """Return a `receive` that yields the buffered body, then delegates."""
    sent = False

    async def replay() -> Any:
        nonlocal sent
        if not sent:
            sent = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        return await receive()

    return replay


def _is_jsonrpc_request(body: bytes) -> bool:
    """True for a JSON-RPC message that expects a response (has an `id`).

    Notifications and SSE polls are excluded: they are part of an existing
    exchange, not a new call worth charging against the limit.
    """
    if not body:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if isinstance(payload, list):
        return any(_message_has_id(item) for item in payload)
    return _message_has_id(payload)


def _message_has_id(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("id") is not None


async def _run_capturing_status(    app: ASGIApp, scope: Scope, receive: Receive, send: Send
) -> int:
    """Run the downstream app and report the status it actually sent."""
    captured = 0

    async def wrapped(message: Any) -> None:
        nonlocal captured
        if message["type"] == "http.response.start":
            captured = message["status"]
        await send(message)

    await app(scope, receive, wrapped)
    return captured or 200


def _bearer(headers: Headers) -> str | None:
    value = headers.get("authorization") or ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _client_ip(scope: Scope, headers: Headers) -> str | None:
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None
