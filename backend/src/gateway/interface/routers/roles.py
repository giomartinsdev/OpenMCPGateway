"""The role table: list, define, edit, delete."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from gateway.application.services import listing
from gateway.application.services.roles import RoleCatalog
from gateway.domain.filters import ROLE_FILTERS
from gateway.domain.permissions import BUILTIN_ROLES, PERMISSIONS
from gateway.interface.dependencies import CanReadRoles, ContainerDep, PrincipalDep, permits
from gateway.interface.listing import PageQuery, filters
from gateway.interface.schemas import RoleRequest

router = APIRouter()


@router.get("/roles")
async def list_roles(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadRoles,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """Built-in roles first, then a page of the tenant's own. Filter by `name`.

    `permissions` rides along on every page: it is the closed catalog the UI uses
    to render the editor, and it does not page.
    """
    del session
    result = await listing.roles(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, ROLE_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    builtin = [role for role in await RoleCatalog(container).all(
        principal.caller.tenant_id) if role["builtin"]]
    return {
        "roles": builtin + result.items,
        "next": result.next,
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
