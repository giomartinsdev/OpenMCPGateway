"""Admin HTTP API — assembled from one router per concern.

Every endpoint here is a thin translation of a command or a read-model query:
routes parse a body, ask the gate for a permission, and call a use case. Business
rules live in `gateway.application.services`; this package only knows HTTP.

That separation is what keeps the UI and a YAML file genuinely equivalent — both
end up in `CommandHandler` — and what keeps a route readable at a glance.

Responses never include secrets (password hashes, key hashes, upstream OAuth
client secrets). The one exception is an API key's plaintext, which is returned
exactly once at issue time and never stored.
"""

from fastapi import APIRouter

from gateway.interface.routers import (
    auth,
    config,
    gateways,
    members,
    observability,
    roles,
    settings,
    tenants,
)

router = APIRouter(prefix="/api")
for module in (
    auth, tenants, members, roles, gateways, observability, settings, config
):
    router.include_router(module.router)


__all__ = ["router"]
