"""Tenant commands as a use case: creating one, joining one, reading one.

Creating a tenant grants ownership, joining grants the default role, and reading
one exposes the join code. Those are policy, and policy does not belong in a
route body.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.domain.errors import NotFound


async def create(container: Container, caller: Caller, name: str) -> dict[str, Any]:
    """Create a tenant and own it.

    The roles come from the command, not from the caller: the account that
    creates a tenant is its owner by construction, so there is nothing to
    validate against its current permissions (it has none yet).
    """
    return await container.commands.create_tenant(
        name=name, owner_id=caller.operator_id, actor=caller.email
    )


async def join(container: Container, caller: Caller, code: str) -> dict[str, Any]:
    """Join with the code the tenant's owner shared."""
    return await container.commands.join_tenant(
        join_code=code, operator_id=caller.operator_id, actor=caller.email
    )


async def current(container: Container, caller: Caller) -> dict[str, Any]:
    """The tenant this account belongs to, including the code to share.

    Readable by every member: the code is what a colleague needs to join, and
    hiding it from everyone but the owner is what makes "ask someone to add you"
    the only path in.
    """
    tenant = await container.read_models.tenant(caller.tenant)
    if tenant is None:
        raise NotFound("tenant not found")
    return {
        "tenant_id": tenant["id"],
        "name": tenant["name"],
        "join_code": tenant["join_code"],
    }


__all__ = ["create", "current", "join"]
