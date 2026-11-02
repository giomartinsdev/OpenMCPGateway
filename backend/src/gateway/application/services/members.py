"""Who belongs to the tenant, with role names resolved for display.

Two routes needed it and each resolved role names inline; the resolution is not
HTTP, so it is a use case.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.application.services.roles import RoleCatalog


async def members(container: Container, caller: Caller) -> list[dict[str, Any]]:
    roles = {
        role["id"]: role["name"]
        for role in await RoleCatalog(container).all(caller.tenant_id)
    }
    return [
        {**operator, "roles": [roles.get(r, r) for r in operator["role_ids"]]}
        for operator in await container.read_models.list_operators(caller.tenant)
    ]


__all__ = ["members"]
