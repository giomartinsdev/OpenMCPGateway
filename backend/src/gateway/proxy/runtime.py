"""Proxy runtime: one mounted FastMCP app per gateway, rebuilt on config change.

Why this shape:

* **One sub-app per gateway** rather than a single server with a routing tool.
  Each gateway needs its own OAuth issuer (`/g/<slug>`), its own target URL and
  its own allowlist. Mounting gives each a real origin for OAuth discovery, so
  standard MCP clients work without a custom auth story.
* **Guard middleware on the outside** distinguishes 401 (bad/missing credential)
  from 403 (known caller, not allowed) and 429 (over limit) and records every
  request. FastMCP still enforces bearer auth underneath — defence in depth,
  not the only check.
* **Rebuild, not restart.** A config change swaps the mounted app and enters the
  new lifespan before the old one is torn down, so a rolling update loses no
  in-flight session.

Known ceiling (single instance): rate-limit counters are in-process, so N
replicas mean N x the configured limit. Upgrade path: move `RateLimiter` state
to Redis behind the same two methods — see `ponytail:` note below.
"""

from __future__ import annotations

import asyncio
import json
import contextlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.auth import AuthProvider
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.domain.events import AuthMode, IdentityProvider
from gateway.infrastructure.eventstore.projections import ReadModels
from gateway.observability import RequestRecorder


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RateLimiter:
    """Sliding-window counter per (gateway, subject).

    ponytail: in-process only — correct for one replica, `N x limit` at N.
    Swap the two methods for a Redis INCR/EXPIRE pair when you scale out.
    """

    _hits: dict[tuple[str, str], deque[float]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def allow(self, gateway_id: str, subject: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        window = now - 60.0
        async with self._lock:
            bucket = self._hits[(gateway_id, subject)]
            while bucket and bucket[0] < window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def sweep(self) -> None:
        """Drop empty buckets so a long-lived process does not leak memory."""
        for key in [k for k, v in self._hits.items() if not v]:
            del self._hits[key]


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    display: str = ""
    method: str = "anonymous"


class IdentityDenied(LookupError):
    """Credential was present but rejected."""


class GatewayNotReady(RuntimeError):
    """The gateway is configured but cannot serve yet (missing credentials).

    A draft in this state is kept out of the route table entirely, so the route
    answers 404 instead of becoming an accidentally public proxy.
    """


class GatewayAuth:
    """Resolves a bearer token into an Identity for one gateway.

    For OAuth gateways this delegates to the FastMCP provider's own
    `verify_token`, so there is exactly one implementation of "is this token
    good" and the proxy layer cannot drift from the transport layer.
    """

    def __init__(self, config: dict[str, Any], read_models: ReadModels,
                 provider: AuthProvider | None) -> None:
        self._config = config
        self._read_models = read_models
        self._provider = provider
        self.mode = AuthMode(config["auth_mode"])

    async def resolve(self, token: str | None) -> Identity | None:
        """Return the caller's identity, or None when no credential is valid."""
        if self.mode is AuthMode.NONE:
            return Identity(subject="anonymous", method="none")
        if not token:
            return None
        if self.mode is AuthMode.API_KEY:
            from gateway.infrastructure.security import hash_api_key

            subject = await self._read_models.subject_for_key_hash(
                self._config["id"], hash_api_key(token)
            )
            return Identity(subject=subject, method="api_key") if subject else None
        if self._provider is None:
            return None
        access = await self._provider.verify_token(token)
        if access is None:
            return None
        claims = getattr(access, "claims", {}) or {}
        # Prefer a human-stable identifier so allowlists stay readable.
        subject = (
            claims.get("login")
            or claims.get("email")
            or claims.get("preferred_username")
            or access.subject
            or access.client_id
        )
        return Identity(
            subject=str(subject),
            display=str(claims.get("name") or claims.get("email") or ""),
            method=self.mode.value,
        )


# --------------------------------------------------------------------------
# Guard middleware
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Provider construction
# --------------------------------------------------------------------------


def build_auth_provider(
    config: dict[str, Any], *, base_url: str, credentials: dict[str, str]
) -> AuthProvider | None:
    """Instantiate the OAuth proxy for a gateway, or None for non-OAuth modes.

    Only OAuth modes get a provider: API keys are verified by the gateway itself
    and need no discovery document, and public routes need no auth at all.

    Raises `GatewayNotReady` when an OAuth gateway has no credentials yet. That is
    a normal intermediate state — you create a gateway, then paste the client id —
    and it must fail *closed*: falling back to "no provider" would silently turn
    an OAuth route into an unauthenticated one.
    """
    mode = AuthMode(config["auth_mode"])
    if mode is not AuthMode.OAUTH:
        return None

    idp = IdentityProvider(config["idp"])
    if not credentials.get("client_id"):
        raise GatewayNotReady("OAuth is selected but no client_id is configured")
    if idp is IdentityProvider.OIDC and not credentials.get("config_url"):
        raise GatewayNotReady("OIDC is selected but no discovery URL is configured")

    issuer = f"{base_url.rstrip('/')}/g/{config['slug']}"
    scopes = list(config.get("required_scopes") or []) or None
    jwt_key = credentials.get("jwt_signing_key") or ""
    # FastMCP persists dynamic client registrations to ~/.local/share by default.
    # An in-memory store keeps the container root-filesystem read-only and means a
    # stateless pod: registrations are re-created on demand, which is what a client
    # does anyway when its registration stops resolving.
    from key_value.aio.stores.memory import MemoryStore

    client_storage = MemoryStore()

    common = {
        "client_id": credentials["client_id"],
        "client_secret": credentials.get("client_secret") or "",
        "base_url": issuer,
        "required_scopes": scopes,
        "jwt_signing_key": jwt_key,
        "cache_ttl_seconds": 300,
        "client_storage": client_storage,
    }

    match idp:
        case IdentityProvider.GITHUB:
            from fastmcp.server.auth.providers.github import GitHubProvider

            return GitHubProvider(**common)
        case IdentityProvider.GOOGLE:
            from fastmcp.server.auth.providers.google import GoogleProvider

            return GoogleProvider(
                **{**common, "required_scopes": scopes or ["openid", "email"]}
            )
        case IdentityProvider.AZURE:
            from fastmcp.server.auth.providers.azure import AzureProvider

            return AzureProvider(
                **common,
                tenant_id=credentials.get("tenant_id", "common"),
            )
        case IdentityProvider.DISCORD:
            from fastmcp.server.auth.providers.discord import DiscordProvider

            return DiscordProvider(**common)
        case IdentityProvider.OIDC:
            from fastmcp.server.auth.oidc_proxy import OIDCProxy

            return OIDCProxy(
                config_url=credentials["config_url"],
                client_id=credentials["client_id"],
                client_secret=credentials.get("client_secret") or "",
                base_url=issuer,
                jwt_signing_key=jwt_key,
            )
    raise ValueError(f"unsupported identity provider {idp}")


# --------------------------------------------------------------------------
# Gateway app assembly
# --------------------------------------------------------------------------


@dataclass(slots=True)
class MountedGateway:
    slug: str
    signature: str
    app: Starlette
    lifespan: Any
    gateway_id: str


def signature_of(config: dict[str, Any], credentials: dict[str, str]) -> str:
    """Cheap change detector — rebuild only when something that matters moved."""
    parts = [
        config["id"],
        config.get("target_url") or "",
        config.get("auth_mode") or "",
        config.get("idp") or "",
        config.get("client_id") or "",
        config.get("config_url") or "",
        ",".join(config.get("required_scopes") or []),
        credentials.get("client_secret") or "",
        credentials.get("tenant_id") or "",
        ",".join(sorted(config.get("allowed_subjects") or [])),
    ]
    import hashlib

    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def build_gateway_app(
    *,
    config: dict[str, Any],
    credentials: dict[str, str],
    read_models: ReadModels,
    limiter: RateLimiter,
    recorder: RequestRecorder,
    base_url: str,
) -> tuple[Starlette, Any]:
    """Build one gateway's app plus the lifespan that must run while it serves.

    The app answers two path shapes, which is why it is not mounted with a
    prefix:

      * ``/mcp``                    after the router strips ``/g/<slug>``
      * ``/.well-known/...``        at the root, where RFC 8414/9728 require it

    The OAuth metadata advertises root-relative URLs, so serving discovery under
    the mount would send clients to a 404.
    """
    provider = build_auth_provider(config, base_url=base_url, credentials=credentials)
    auth = GatewayAuth(config, read_models, provider)

    proxy: FastMCP = create_proxy(
        config["target_url"],
        name=f"gateway:{config['slug']}",
        auth=provider,
        provider_error_strategy="raise",
    )
    mcp_app = proxy.http_app(path="/mcp")

    guarded = GuardMiddleware(
        mcp_app, config=config, auth=auth, limiter=limiter,
        recorder=recorder, base_url=base_url,
    )

    routes: list[Any] = []
    if provider is not None:
        # `mcp_path` is the internal path; the mount prefix lives in `base_url`.
        routes.extend(provider.get_well_known_routes(mcp_path="/mcp"))
    routes.append(Route("/mcp", endpoint=guarded,
                        methods=["GET", "POST", "DELETE"]))

    app = Starlette(routes=routes)
    return app, mcp_app.lifespan


__all__ = [
    "GatewayAuth",
    "GuardMiddleware",
    "Identity",
    "MountedGateway",
    "RateLimiter",
    "build_gateway_app",
    "build_auth_provider",
    "signature_of",
]
