"""The panel's identity read: who is signed in, and what they may do.

`/auth/me` used to assemble this inline from five read-model calls. Three screens
render it and the login flow branches on it, so it is a use case with a name.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.application.services.roles import RoleCatalog
from gateway.domain.permissions import DEFAULT_ROLE_IDS, OWNER_ROLE


async def identity(container: Container, caller: Caller) -> dict[str, Any]:
    role_ids = await container.read_models.operator_roles(caller.operator_id)
    if role_ids is None:
        role_ids = (OWNER_ROLE,) if caller.is_admin else DEFAULT_ROLE_IDS
    tenant = (
        await container.read_models.tenant(caller.tenant_id)
        if caller.tenant_id
        else None
    )
    roles = RoleCatalog(container)
    names = (
        {role["id"]: role["name"] for role in await roles.all(caller.tenant_id)}
        if caller.tenant_id
        else {}
    )
    return {
        "email": caller.email,
        "operator_id": caller.operator_id,
        "tenant_id": caller.tenant_id,
        "tenant_name": tenant.name if tenant else None,
        "role_ids": list(role_ids),
        "roles": [names.get(role_id, role_id) for role_id in role_ids],
        "permissions": sorted(caller.permissions),
        # What the login flow renders instead of a page it cannot fill. A panel
        # with no tenant has nothing to show, so the choice *is* the page.
        "onboarding": ["create", "join"] if not caller.onboarded else [],
        # Kept for the pages written before permissions, and it means exactly
        # "owner" of the current tenant.
        "is_admin": OWNER_ROLE in role_ids and caller.onboarded,
    }


__all__ = ["identity"]
