"""The role table: list, define, edit, delete."""

from typing import Any

from fastapi import APIRouter, HTTPException

from gateway.application.services.roles import RoleCatalog
from gateway.domain.permissions import BUILTIN_ROLES, PERMISSIONS
from gateway.interface.dependencies import CanReadRoles, ContainerDep, PrincipalDep, permits
from gateway.interface.schemas import RoleRequest

router = APIRouter()


@router.get("/roles")
async def list_roles(session: CanReadRoles, principal: PrincipalDep,
                     container: ContainerDep) -> dict[str, Any]:
    del session
    return {
        "roles": await RoleCatalog(container).all(principal.caller.tenant_id),
        "permissions": list(PERMISSIONS),
    }


@router.post("/roles", status_code=201)
async def define_role(payload: RoleRequest, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    permits(principal, "roles:write")
    caller = principal.caller
    RoleCatalog(container).ensure_grantable(caller, payload.permissions)
    role_id = await container.commands.define_role(
        tenant_id=caller.tenant,
        name=payload.name,
        permissions=payload.permissions,
        actor=caller.email,
    )
    return {"id": role_id}


@router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: RoleRequest, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    permits(principal, "roles:write")
    if role_id in BUILTIN_ROLES:
        raise HTTPException(status_code=422, detail="built-in roles cannot be edited")
    caller = principal.caller
    RoleCatalog(container).ensure_grantable(caller, payload.permissions)
    await container.commands.define_role(
        tenant_id=caller.tenant,
        role_id=role_id,
        name=payload.name,
        permissions=payload.permissions,
        actor=caller.email,
    )
    return {"status": "updated"}


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    permits(principal, "roles:write")
    if role_id in BUILTIN_ROLES:
        raise HTTPException(status_code=422, detail="built-in roles cannot be deleted")
    caller = principal.caller
    holders = await container.read_models.role_holder_count(role_id, caller.tenant)
    if holders:
        raise HTTPException(
            status_code=422,
            detail=f"role is assigned to {holders} member(s); reassign them first",
        )
    await container.commands.delete_role(role_id, actor=caller.email)
    return {"status": "deleted"}


__all__ = ["router"]
