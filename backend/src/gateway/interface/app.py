"""Top-level ASGI dispatcher.

Two things are dynamic at runtime and both live here:

* the set of gateways (`/g/<slug>/mcp`) — rebuilt from the event log
* their OAuth discovery documents, which must be served from the **root**
  (`/.well-known/oauth-protected-resource/g/<slug>/mcp`) because that is the
  URL embedded in the metadata clients read

Everything else is the FastAPI admin app and the built SPA.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from gateway.application.container import Container
from gateway.config import Settings
from gateway.interface.api import router as api_router

# default: backend/src/gateway/interface/app.py → repo root, where `web/dist` is
# built. OMG_WEB_DIST overrides it for an image layout that differs.
WEB_DIST = Path(
    os.environ.get("OMG_WEB_DIST") or Path(__file__).resolve().parents[4] / "web" / "dist"
)
SPA_FALLBACK = {
    "message": "Frontend not built. Run `npm --prefix web ci && npm --prefix web run build`, "
               "or use `npm --prefix web run dev` and set OMG_CORS_ORIGINS.",
}


def create_admin_app(settings: Settings) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = await Container.create(settings)
        try:
            yield
        finally:
            await app.state.container.close()
            app.state.container = None

    app = FastAPI(
        title="OpenMCPGateway",
        version="0.1.0",
        docs_url="/api/docs",
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

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        container: Container | None = app.state.container
        if container is None:
            return {"status": "starting"}
        pending = container.router.pending
        return {
            "status": "ok",
            "gateways": len(container.router.entries),
            # Empty in the normal case; a name here means "enabled but not
            # serving", which is the state that used to look like a typo'd URL.
            "pending": pending,
        }

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
    """Gateway traffic first; discovery and admin routes otherwise."""

    def __init__(self, admin_app: FastAPI) -> None:
        self.admin_app = admin_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            container: Container | None = getattr(
                self.admin_app.state, "container", None
            )
            if container is not None and container.router.owns(scope.get("path", "")):
                await container.router(scope, receive, send)
                return
        await self.admin_app(scope, receive, send)


def build_asgi(settings: Settings) -> GatewayDispatcher:
    return GatewayDispatcher(create_admin_app(settings))


__all__ = ["GatewayDispatcher", "build_asgi", "create_admin_app"]
