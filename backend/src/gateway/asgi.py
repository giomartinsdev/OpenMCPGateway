"""Application entrypoint.

The dispatcher decides between gateway traffic and the admin app *once*, so the
root Starlette app only needs to know about `/.well-known` and `/api`; everything
else falls through to the SPA.
"""

from gateway.application.container import Container
from gateway.config import Settings
from gateway.interface.app import GatewayDispatcher, create_admin_app
from gateway.log import configure


def build_asgi(settings: Settings) -> GatewayDispatcher:
    # Before the app is built: uvicorn and SQLAlchemy log during startup, and a
    # format change halfway through leaves the first lines unparseable.
    configure(settings.log_level)
    return GatewayDispatcher(create_admin_app(settings))


def _container(dispatcher: GatewayDispatcher) -> Container | None:
    return getattr(dispatcher.admin_app.state, "container", None)


__all__ = ["GatewayDispatcher", "build_asgi", "create_admin_app"]
