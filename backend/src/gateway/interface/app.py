"""Top-level ASGI dispatcher.

Two things are dynamic at runtime and both live here:

* the set of gateways (`/g/<slug>/mcp`) — rebuilt from the event log
* their OAuth discovery documents, which must be served from the **root**
  (`/.well-known/oauth-protected-resource/g/<slug>/mcp`) because that is the
  URL embedded in the metadata clients read

Everything else is the FastAPI admin app and the built SPA.
"""

import contextlib
import importlib.metadata
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from gateway.application.container import Container
from gateway.config import Settings
from gateway.interface import errors
from gateway.interface.api import router as api_router
from gateway.interface.middleware import log_requests
from gateway.log import log
from gateway.proxy import branding

# default: backend/src/gateway/interface/app.py → repo root, where `web/dist` is
# built. OMG_WEB_DIST overrides it for an image layout that differs.
WEB_DIST = Path(
    os.environ.get("OMG_WEB_DIST") or Path(__file__).resolve().parents[4] / "web" / "dist"
)
SPA_FALLBACK = {
    "message": "Frontend not built. Run `npm --prefix web ci && npm --prefix web run build`, "
               "or use `npm --prefix web run dev` and set OMG_CORS_ORIGINS.",
}


def _version() -> str:
    """Whatever was installed. The panel footer and `/api/docs` read it."""
    try:
        return importlib.metadata.version("openmcp-gateway")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def create_admin_app(settings: Settings) -> FastAPI:
    # FastMCP's consent and error pages are rendered by module-level functions it
    # imports by name, so there is no constructor argument to pass a template
    # through. Rebinding them here — once per process, at the composition root —
    # keeps that detail out of every call site.
    branding.install()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A caller that already built a container (tests, an embedding) keeps it:
        # building a second one here would leak its pool and serve requests from
        # the fixture's container while this one owned the storage.
        owned = app.state.container is None
        if owned:
            log.info("app.starting", base_url=settings.base_url)
            app.state.container = await Container.create(settings)
        try:
            yield
        finally:
            if owned:
                await app.state.container.close()
                app.state.container = None
                log.info("app.stopped")

    app = FastAPI(
        title="OpenMCPGateway",
        version=_version(),
        # `/api/docs` is the manual — a product surface an operator reads. Swagger
        # gets out of its way; the OpenAPI document keeps its address.
        docs_url="/api/swagger",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = None
    app.state.extra_routes = []

    if settings.cors_origins:
        # Only needed for `npm run dev`; in production the SPA is same-origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.middleware("http")(log_requests)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        container: Container | None = app.state.container
        if container is None:
            return {"status": "starting"}
        pending = container.router.pending
        return {
            "status": "ok",
            # Serving gateways, not routes: one OAuth gateway owns several
            # routes (metadata, login endpoints, /mcp), so counting routes made
            # this number disagree with the gateway list for no reason.
            "gateways": len(container.router.live),
            # Empty in the normal case; a name here means "enabled but not
            # serving", which is the state that used to look like a typo'd URL.
            "pending": pending,
        }

    errors.install(app)
    app.include_router(api_router)

    if WEB_DIST.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=WEB_DIST / "assets", check_dir=False),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Any:
            candidate = (WEB_DIST / full_path).resolve()
            if full_path and candidate.is_file() and WEB_DIST in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_missing() -> Any:
            return JSONResponse(SPA_FALLBACK, status_code=503)

    return app


class GatewayDispatcher:
    """Gateway traffic first; the console MCP; discovery and admin routes otherwise.

    The console is mounted lazily for the same reason the gateways are: it needs
    the container, and the container needs the app's lifespan to have run. Building
    it on the first request keeps one startup path instead of two.
    """

    def __init__(self, admin_app: FastAPI) -> None:
        self.admin_app = admin_app
        self._console: Any = None

    def _console_app(self, container: Container) -> Any:
        if self._console is None:
            from gateway.console.mount import console_app

            self._console = console_app(container)
        return self._console

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            container: Container | None = getattr(
                self.admin_app.state, "container", None
            )
            path = scope.get("path", "")
            if container is not None:
                if container.router.owns(path):
                    await container.router(scope, receive, send)
                    return
                if path == "/mcp" or path.startswith("/mcp/"):
                    await self._console_app(container)(scope, receive, send)
                    return
        await self.admin_app(scope, receive, send)


def build_asgi(settings: Settings) -> GatewayDispatcher:
    return GatewayDispatcher(create_admin_app(settings))


__all__ = ["GatewayDispatcher", "build_asgi", "create_admin_app"]
