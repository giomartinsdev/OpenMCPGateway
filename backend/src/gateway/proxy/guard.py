"""The ASGI middleware in front of every gateway mount.

It distinguishes 401 (bad/missing credential) from 403 (known caller, not
allowed) and 429 (over limit), and records every request. FastMCP still enforces
bearer auth underneath — defence in depth, not the only check.
"""

import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.log import log
from gateway.observability import Metrics, RequestRecorder
from gateway.proxy.identity import GatewayAuth, Identity, is_key_subject
from gateway.proxy.payload import preview
from gateway.proxy.rate_limit import RateLimiter

_RESOURCE_METADATA = "/.well-known/oauth-protected-resource"

#: Largest response body the guard will keep a copy of.
#:
#: The response is *streamed* to the caller; this only decides how much of it is
#: retained to build a preview and a byte count. Past the cap the bytes are still
#: forwarded and still counted — dropping them from the preview is the only thing
#: that changes, because buffering a whole SSE stream to describe it would trade
#: a debugging aid for the memory the body cap just bought.
_RESPONSE_PREVIEW_BYTES = 256 * 1024

class GuardMiddleware:
    """Auth + authorisation + rate limit + observation for one gateway mount."""

    def __init__(self, app: ASGIApp, *, config: dict[str, Any],
                 auth: GatewayAuth, limiter: RateLimiter,
                 recorder: RequestRecorder, metrics: Metrics, base_url: str,
                 read_models: Any = None, max_body_bytes: int = 1_048_576,
                 trusted_proxies: tuple[str, ...] = ()) -> None:
        self.app = app
        self.config = config
        self.auth = auth
        self.limiter = limiter
        self.recorder = recorder
        self.metrics = metrics
        #: Only touched when the gateway has a monthly quota; a gateway without
        #: one never reads it.
        self.read_models = read_models
        self.base_url = base_url.rstrip("/")
        self.prefix = f"/g/{config['slug']}"
        self.inner_path = "/mcp"
        #: The body is buffered so it can be inspected, so this is also the
        #: largest allocation one call can cause.
        self.max_body_bytes = max_body_bytes
        #: Peers whose `X-Forwarded-For` is believed. Empty means none.
        self.trusted_proxies = trusted_proxies

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
        #: Why this call was refused. Empty for an allowed call, which needs no
        #: explanation. `outcome` says "forbidden"; this says which gate.
        reason = ""
        tool: str | None = None
        #: The payload trace. Filled in as the call proceeds, recorded in the
        #: `finally` whether it succeeded, was refused, or raised.
        request_body = b""
        bytes_out = 0
        response_body = b""
        #: Minted at the edge when the caller sent none, so two anonymous calls
        #: never share a row. Length-capped: a header is attacker-controlled and
        #: ends up in a database column and a log line.
        request_id = _request_id(headers)
        client_ip = _client_ip(scope, headers, self.trusted_proxies)

        try:
            identity = await self.auth.resolve(_bearer(headers))
            if identity is None:
                outcome, status = "unauthenticated", 401
                reason = "missing or invalid credential"
                response = self._unauthorized()
            elif not self._authorized(identity.subject):
                outcome, status = "forbidden", 403
                reason = "subject not in allowlist"
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
                body, too_large = await _read_body(
                    receive, self.max_body_bytes
                )
                if too_large:
                    # Refused before the body is buffered in full: the point of
                    # the cap is to not hold it, so the overflow is dropped as it
                    # arrives rather than collected and then rejected.
                    outcome, status = "forbidden", 413
                    reason = "request body too large"
                    response = JSONResponse(
                        {
                            "error": "payload_too_large",
                            "message": f"body exceeds {self.max_body_bytes} bytes",
                        },
                        status_code=413,
                    )
                else:
                    request_body = body
                    receive = _replay(body, receive)
                    tool = _called_tool(body)
                    blocked = self._blocked_tool(body, identity.source)
                    if blocked is not None:
                        # A tool outside the allowlist is a 403 with the name in
                        # it, not a silent removal from `tools/list`: the caller
                        # asked for something specific, and "unknown tool" would
                        # send it looking for a typo in its own config.
                        outcome, status = "forbidden", 403
                        reason = "tool not in allowlist"
                        response = JSONResponse(
                            {
                                "error": "forbidden",
                                "message": f"tool '{blocked}' is not exposed by gateway "
                                           f"'{self.config['slug']}'",
                            },
                            status_code=403,
                        )
                    # Rate limiting counts MCP *requests* (JSON-RPC messages that
                    # carry an id), not HTTP POSTs: one MCP call is a handshake
                    # plus a call on the wire, and charging for the transport
                    # would make a limit of N mean fewer than N calls to the user.
                    elif _is_jsonrpc_request(body) and (
                        refusal := await self._refusal(identity.subject)
                    ) is not None:
                        outcome, status, reason = refusal
                        response = JSONResponse(
                            {"error": outcome, "message": reason},
                            status_code=status,
                            headers={"Retry-After": "60"} if status == 429 else None,
                        )
                    else:
                        status, bytes_out, response_body = await _run_traced(
                            self.app, self._rewrite(scope), receive, send, request_id
                        )
        except Exception:
            outcome = "error"
            status = 502
            reason = "upstream error"
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "proxy.request",
                gateway=self.config["slug"],
                subject=identity.subject if identity else "anonymous",
                outcome=outcome,
                status=status,
                duration_ms=duration_ms,
                ip=client_ip,
                method=scope.get("method", "POST"),
                bytes_in=len(request_body),
                bytes_out=bytes_out,
            )
            self.metrics.observe(
                gateway=self.config["slug"],
                outcome=outcome,
                status=status,
                duration_ms=duration_ms,
            )
            await self.recorder.record(
                gateway_id=self.config["id"],
                subject=identity.subject if identity else "anonymous",
                outcome=outcome,
                status=status,
                duration_ms=duration_ms,
                ip=client_ip,
                tool=tool,
                method=scope.get("method", "POST"),
                reason=reason,
                request_id=request_id,
                gateway_slug=self.config["slug"],
                bytes_in=len(request_body),
                bytes_out=bytes_out,
                request_preview=preview(request_body),
                response_preview=preview(response_body),
            )

        if response is not None:
            # Refusals are the tickets support gets, so the id goes on the error
            # too — minting it only on the success path would make it useless
            # exactly when it is needed.
            response.headers["X-Request-Id"] = request_id
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

    async def _refusal(self, subject: str) -> tuple[str, int, str] | None:
        """Why this call may not proceed, or None when it may.

        The per-minute limiter charges the burst; the monthly quota answers "is
        this integration over its bill". Both are decided before the call
        reaches the upstream, so a refusal costs no upstream work. The quota is
        read only for gateways that set one — a gateway without a ceiling must
        not pay a query per request.
        """
        if not await self.limiter.allow(
            self.config["id"],
            subject,
            int(self.config.get("requests_per_minute") or 0),
        ):
            return "rate_limited", 429, "rate limit exceeded"
        # Reserved atomically before the call goes out: a counter incremented by
        # the async recorder would let two calls in a row read the same count and
        # both pass.
        quota = int(self.config.get("monthly_quota") or 0)
        if (
            quota > 0
            and self.read_models is not None
            and not await self.read_models.consume_quota(
                self.config["id"], month=_this_month(), quota=quota
            )
        ):
            return "rate_limited", 429, "monthly quota exhausted"
        return None

    def _authorized(self, subject: str) -> bool:
        allowed: list[str] = self.config.get("allowed_subjects") or []
        if not allowed or subject in allowed or "*" in allowed:
            return True
        # A key is a credential the operator minted for *this* gateway, and its
        # subject is derived from the key id. Listing it would make every
        # rotation a silent lockout, so the allowlist — a gate on identities a
        # provider vouched for — does not apply. `resolve` only produces this
        # subject after the hash matched a live key.
        return is_key_subject(subject)

    def _blocked_tool(self, body: bytes, source: str) -> str | None:
        """The tool name this request asks for that the gateway does not expose.

        Only `tools/call` is checked here. `tools/list` is filtered by the
        server itself (a `Visibility` transform), because that answer is built
        from the upstream catalog and rewriting it mid-stream would mean parsing
        SSE frames in the guard.

        The list is resolved per credential source: a gateway that admits a bot
        with a key and a person through the IdP can give each its own catalog
        without publishing two gateways to the same upstream.
        """
        allowed = (self.config.get("tool_scopes") or {}).get(source)
        if not allowed:
            allowed = self.config.get("tools") or []
        if not allowed or not body:
            return None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("method") != "tools/call":
                continue
            name = (message.get("params") or {}).get("name")
            if isinstance(name, str) and name not in allowed:
                return name
        return None

    def _unauthorized(self) -> JSONResponse:
        if self.auth.hosts_login:
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


async def _read_body(
    receive: Receive, limit: int = 1_048_576,
) -> tuple[bytes, bool]:
    """Consume and buffer the request body so it can be inspected and replayed.

    `receive` is a one-shot stream: reading it for rate limiting would leave the
    downstream app with an empty body, so the bytes are captured here and handed
    back through `_replay`.

    Returns `(body, too_large)`. Past `limit` the remaining chunks are read and
    discarded rather than accumulated: the point of the cap is to not hold the
    body, so collecting it first and rejecting it after would defeat it. The
    stream still has to be drained, or the client sees a connection reset
    instead of the 413 that explains why.
    """
    chunks: list[bytes] = []
    total = 0
    too_large = False
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            too_large = True
        elif not too_large:
            chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return (b"" if too_large else b"".join(chunks)), too_large


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


def _called_tool(body: bytes) -> str | None:
    """The tool a `tools/call` names, so the log can say what was called.

    `tools/list` and the handshake name nothing, and a batch may carry several
    calls; the first name is enough for a traffic row that already collapses a
    request into one line.
    """
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict) or message.get("method") != "tools/call":
            continue
        name = (message.get("params") or {}).get("name")
        if isinstance(name, str) and name:
            return name
    return None


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


async def _run_traced(
    app: ASGIApp, scope: Scope, receive: Receive, send: Send,
    request_id: str = "",
) -> tuple[int, int, bytes]:
    """Run the downstream app; report status, body bytes and a retained prefix.

    The header is added by wrapping `send` rather than by wrapping the response:
    the proxy streams SSE, and rebuilding a streamed response to add one header
    would mean buffering it.

    Response bytes are counted in full and retained only up to
    `_RESPONSE_PREVIEW_BYTES`. Everything past that is forwarded untouched and
    still counted, so the number in the log stays exact while the memory cost of
    describing it stays bounded.
    """
    captured = 0
    total = 0
    kept = bytearray()

    async def wrapped(message: Any) -> None:
        nonlocal captured, total
        if message["type"] == "http.response.start":
            captured = message["status"]
            if request_id:
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            total += len(chunk)
            room = _RESPONSE_PREVIEW_BYTES - len(kept)
            if room > 0 and chunk:
                kept.extend(chunk[:room])
        await send(message)

    await app(scope, receive, wrapped)
    return captured or 200, total, bytes(kept)


def _this_month() -> str:
    """`YYYY-MM` in UTC, the same spelling the projection writes."""
    return datetime.now(UTC).strftime("%Y-%m")


_REQUEST_ID_HEADER = "x-request-id"

def _request_id(headers: Headers) -> str:
    """The caller's id, or one we mint. Bounded either way.

    Echoing an arbitrary header back verbatim is how a log line grows to a
    megabyte, so a value that is too long or is not a safe token is replaced
    rather than refused: a bad header must not fail a working MCP call.
    """
    supplied = (headers.get(_REQUEST_ID_HEADER) or "").strip()
    if supplied and len(supplied) <= 128 and all(
        char.isalnum() or char in "-_.:" for char in supplied
    ):
        return supplied
    return f"req_{secrets.token_hex(8)}"


def _bearer(headers: Headers) -> str | None:
    value = headers.get("authorization") or ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _client_ip(
    scope: Scope, headers: Headers, trusted_proxies: tuple[str, ...] = (),
) -> str | None:
    """The caller's address, believing `X-Forwarded-For` only when it may.

    The header is written by whoever is talking to us. Trusting it from an
    arbitrary peer turns the audit trail into something the caller can dictate —
    which matters here because the traffic log is the evidence for "who called
    this", not a display field.

    `trusted_proxies` names the peers allowed to speak for someone else. The
    *left-most* entry is taken, because that is the address the outermost proxy
    observed; the rest are the hops it traversed and are just as forgeable.
    """
    forwarded = headers.get("x-forwarded-for")
    if forwarded and _peer_trusted(scope, trusted_proxies):
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = scope.get("client")
    return client[0] if client else None

def _peer_trusted(scope: Scope, trusted_proxies: tuple[str, ...]) -> bool:
    """Whether the immediate peer is one whose forwarding headers are believed.

    `*` trusts everyone, which is only correct when nothing but the proxy can
    reach the port — the same trade a `trusted_hosts="*"` makes, and the reason
    it is opt-in rather than the default.
    """
    if not trusted_proxies:
        return False
    if "*" in trusted_proxies:
        return True
    client = scope.get("client")
    return bool(client) and client[0] in trusted_proxies
