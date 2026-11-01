"""Application entrypoint.

The dispatcher decides between gateway traffic and the admin app *once*, so the
root Starlette app only needs to know about `/.well-known` and `/api`; everything
else falls through to the SPA.
"""

from __future__ import annotations

from starlette.types import Receive, Scope, Send

from gateway.application.container import Container
from gateway.config import Settings
from gateway.interface.app import GatewayDispatcher, create_admin_app


def build_asgi(settings: Settings) -> GatewayDispatcher:
    return GatewayDispatcher(create_admin_app(settings))


def _container(dispatcher: GatewayDispatcher) -> Container | None:
    return getattr(dispatcher.admin_app.state, "container", None)


__all__ = ["build_asgi", "GatewayDispatcher", "create_admin_app"]
