"""Gateway router — keeps live gateway apps in sync with the event log.

`refresh()` is the only mutation point: called after every command, and lazily on
an unknown slug so a gateway created by another replica becomes reachable
without a restart.

Each gateway contributes **absolute** routes to the shared router:
`/g/<slug>/mcp` and, for OAuth gateways, the root-level discovery documents
(`/.well-known/...`). Absolute paths are what make per-gateway OAuth possible:
the metadata advertises root-relative URLs, so the routes must be registered at
the root rather than under a mount prefix.

Rebuilds are bounded by a config signature: only gateways whose route-relevant
config changed get a new app, and the new app's lifespan is entered *before* the
swap so no in-flight session observes a torn-down router.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP
from fastmcp.server import create_proxy
from starlette.routing import Route, Router
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.infrastructure.eventstore.postgres import PostgresEventStore
from gateway.infrastructure.eventstore.projections import ReadModels
from gateway.observability import RequestRecorder
from gateway.proxy.runtime import (
    GatewayAuth,
    GatewayNotReady,
    GuardMiddleware,
    RateLimiter,
    build_auth_provider,
)


@dataclass(slots=True)
class LiveGateway:
    slug: str
    gateway_id: str
    signature: str
    routes: list[Route]
    # The proxy's lifespan runs in its own task, not in whoever happened to call
    # `refresh()`. FastMCP keeps ContextVar tokens across the lifespan body, and a
    # token may only be reset in the context that set it — so entering it inside a
    # request task makes shutdown raise "created in a different Context", and the
    # route table silently loses the gateway.
    lifespan: LifespanTask | None


class LifespanTask:
    """A lifespan entered and exited on one dedicated task."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._ready: asyncio.Future[None] | None = None
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None

    async def astart(self) -> None:
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._stop = asyncio.Event()
        self._task = loop.create_task(self._run())
        await self._ready
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        assert self._ready is not None and self._stop is not None
        try:
            async with self._factory():
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            self._error = exc
            if not self._ready.done():
                self._ready.set_result(None)

    async def astop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
            self._task = None


@dataclass(slots=True)
class GatewayRouter:
    read_models: ReadModels
    limiter: RateLimiter
    recorder: RequestRecorder
    base_url: str
    store: PostgresEventStore
    _live: dict[str, LiveGateway] = field(default_factory=dict)
    # slug -> reason it is not serving yet; served by /healthz and the API so a
    # silent 404 has an explanation next to it.
    _pending: dict[str, str] = field(default_factory=dict)
    _router: Router = field(default_factory=lambda: Router(routes=[]))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def entries(self) -> list[Route]:
        """Currently published routes (health endpoint reports the count)."""
        return list(self._router.routes)

    @property
    def pending(self) -> dict[str, str]:
        """Gateways that are enabled but not serving, and why."""
        return dict(self._pending)

    # -- lifecycle --------------------------------------------------------

    async def refresh(self) -> None:
        """Reconcile live gateways with the read model."""
        gateways = await self.read_models.list_gateways()
        async with self._lock:
            desired: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
            for gateway in gateways:
                if gateway["deleted"] or not gateway["enabled"]:
                    continue
                principals = await self.read_models.principals(gateway["id"])
                desired[gateway["slug"]] = (
                    # The allowlist participates in the signature: without it, an
                    # allow/revoke command would leave the old guard in place and
                    # the change would only take effect after a restart.
                    {
                        **gateway,
                        "allowed_subjects": [p["subject"] for p in principals],
                    },
                    await self._credentials(gateway["id"]),
                )

            for slug in list(self._live):
                if slug not in desired:
                    await self._drop(slug)

            for slug, (gateway, credentials) in desired.items():
                signature = _signature(gateway, credentials)
                live = self._live.get(slug)
                if live is not None and live.signature == signature:
                    continue
                try:
                    await self._install(gateway, credentials, signature)
                except GatewayNotReady as exc:
                    # A draft that is missing credentials is not an error the
                    # operator can act on from here, and one incomplete gateway
                    # must never take the others down with it.
                    await self._drop(slug)
                    self._pending[slug] = str(exc)
                    continue
                self._pending.pop(slug, None)

            self._publish()

    async def shutdown(self) -> None:
        async with self._lock:
            for slug in list(self._live):
                await self._drop(slug)
            self._publish()

    # -- install / teardown ----------------------------------------------

    async def _install(self, gateway: dict[str, Any], credentials: dict[str, str],
                       signature: str) -> None:
        slug = gateway["slug"]
        previous = self._live.get(slug)

        # `refresh` already merged the allowlist in; it is needed on every
        # request, and re-joining per request would be wasteful.
        config = gateway

        provider = build_auth_provider(
            config, base_url=self.base_url, credentials=credentials
        )
        auth = GatewayAuth(config, self.read_models, provider)

        # When the upstream has its own token, the gateway presents it so callers
        # never hold it. `Client` accepts a target dict with headers, which is the
        # documented way to attach fixed headers to every upstream request.
        target: Any = config["target_url"]
        if credentials.get("upstream_token"):
            target = {
                "mcpServers": {
                    slug: {
                        "url": config["target_url"],
                        "transport": "http",
                        "headers": {
                            "Authorization": f"Bearer {credentials['upstream_token']}"
                        },
                    }
                }
            }

        proxy: FastMCP = create_proxy(
            target,
            name=f"gateway:{slug}",
            auth=provider,
            provider_error_strategy="raise",
        )
        mcp_app = proxy.http_app(path="/mcp")
        guarded = GuardMiddleware(
            mcp_app, config=config, auth=auth, limiter=self.limiter,
            recorder=self.recorder, base_url=self.base_url,
        )

        routes: list[Any] = []
        if provider is not None:
            # Discovery documents are registered at their absolute root paths,
            # because that is where the advertised metadata points.
            routes.extend(provider.get_well_known_routes(mcp_path="/mcp"))
        routes.append(
            Route(
                f"/g/{slug}/mcp",
                endpoint=guarded,
                methods=["GET", "POST", "DELETE"],
            )
        )

        # Enter the proxy lifespan before publishing the routes, on its own task
        # (see LifespanTask for why the caller's task will not do).
        lifespan = (
            LifespanTask(lambda: mcp_app.lifespan(mcp_app))
            if mcp_app.lifespan
            else None
        )
        if lifespan is not None:
            await lifespan.astart()

        self._live[slug] = LiveGateway(
            slug=slug, gateway_id=gateway["id"], signature=signature,
            routes=routes, lifespan=lifespan,
        )
        if previous is not None:
            await _close(previous.lifespan)

    async def _drop(self, slug: str) -> None:
        live = self._live.pop(slug, None)
        if live is not None:
            await _close(live.lifespan)

    def _publish(self) -> None:
        """Swap the route table in one assignment — never partially updated."""
        routes: list[Route] = []
        for live in self._live.values():
            routes.extend(live.routes)
        self._router = Router(routes=routes)

    def owns(self, path: str) -> bool:
        """True when this path belongs to gateway traffic (route table or /g/)."""
        return path.startswith("/g/") or path.startswith("/.well-known/") or _matches(
            self._router.routes, path
        )

    # -- dispatch ---------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if not self.owns(path):
            await _not_found(None)(scope, receive, send)
            return
        if _matches(self._router.routes, path):
            await self._router(scope, receive, send)
            return
        slug = _slug(path)
        if not slug:
            await _not_found(None)(scope, receive, send)
            return
        # Unknown route: another replica may have created it a moment ago.
        await self.refresh()
        if _matches(self._router.routes, path):
            await self._router(scope, receive, send)
            return
        if slug and slug in self._pending:
            # Configured but incomplete: say so, because "404" alone reads like a
            # typo in the URL and sends people looking in the wrong place.
            await _not_ready(slug, self._pending[slug])(scope, receive, send)
            return
        await _not_found(slug)(scope, receive, send)

    # -- credentials ------------------------------------------------------

    async def _credentials(self, gateway_id: str) -> dict[str, str]:
        """Upstream OAuth secrets come from the write model, never the read model."""
        events = await self.store.load_stream(gateway_id)
        credentials: dict[str, str] = {}
        for event in events:
            payload = event.payload()
            for key in ("client_id", "client_secret", "config_url", "tenant_id",
                        "upstream_token"):
                value = payload.get(key)
                if value:
                    credentials[key] = value
        credentials["jwt_signing_key"] = _jwt_key(gateway_id)
        return credentials


def _matches(routes: list[Route], path: str) -> bool:
    for route in routes:
        route_path = getattr(route, "path", "")
        if isinstance(route_path, str) and _path_matches(route_path, path):
            return True
    return False


def _path_matches(template: str, path: str) -> bool:
    if "{" not in template:
        return template == path
    # Route templates with params: let Starlette decide the real match.
    import re

    pattern = re.sub(r"\{[^}]+\}", "[^/]+", re.escape(template))
    pattern = pattern.replace(r"\[^/\]\+", "[^/]+")
    return re.fullmatch(pattern, path) is not None


def _jwt_key(gateway_id: str) -> str:
    """Deterministic per-gateway signing key, stable across restarts.

    Derived from the deployment secret: a random key would invalidate every
    issued token on each pod restart, and two replicas must agree.
    """
    import os

    secret = os.environ.get("OMG_SESSION_SECRET", "")
    return hashlib.sha256(f"{secret}:{gateway_id}".encode()).hexdigest()


def _signature(gateway: dict[str, Any], credentials: dict[str, str]) -> str:
    """Cheap change detector — rebuild only when something route-relevant moved."""
    parts = [
        gateway["id"],
        gateway.get("target_url") or "",
        gateway.get("auth_mode") or "",
        gateway.get("idp") or "",
        gateway.get("client_id") or "",
        gateway.get("config_url") or "",
        ",".join(gateway.get("required_scopes") or []),
        credentials.get("client_secret") or "",
        credentials.get("tenant_id") or "",
        # Only whether it exists — the value must not land in a log line.
        "upstream" if credentials.get("upstream_token") else "",
        repr(gateway.get("requests_per_minute")),
        ",".join(sorted(gateway.get("allowed_subjects") or [])),
    ]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def _belongs_to(entry: str, slug: str) -> bool:
    return entry.startswith(f"/g/{slug}/") or f"/g/{slug}/" in entry


def _candidate_prefixes(path: str) -> list[str]:
    """Exact match first, then each shorter path segment boundary."""
    candidates = [path]
    trimmed = path
    while trimmed.count("/") > 0:
        trimmed = trimmed.rsplit("/", 1)[0]
        if trimmed:
            candidates.append(trimmed)
    return candidates


def _slug(path: str) -> str | None:
    if not path.startswith("/g/"):
        return None
    remainder = path[3:]
    slug = remainder.split("/", 1)[0]
    return slug or None


def _not_found(slug: str | None):
    from starlette.responses import JSONResponse

    message = f"no gateway named '{slug}'" if slug else "nothing here"
    return JSONResponse({"error": "not_found", "message": message}, status_code=404)


def _not_ready(slug: str, reason: str):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": "gateway_not_ready", "gateway": slug, "message": reason},
        status_code=503,
    )


async def _close(context: LifespanTask | None) -> None:
    if context is None:
        return
    await context.astop()


__all__ = ["GatewayRouter", "LiveGateway"]
