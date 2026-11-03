"""Members: who belongs, adding an account, and replacing someone's roles."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from gateway.application.services import listing
from gateway.application.services.roles import RoleCatalog
from gateway.domain.filters import MEMBER_FILTERS
from gateway.domain.permissions import DEFAULT_ROLE_IDS, OWNER_ROLE
from gateway.interface.dependencies import (
    CanReadMembers,
    ContainerDep,
    PrincipalDep,
    permits,
)
from gateway.interface.listing import PageQuery, filters
from gateway.interface.schemas import OperatorCreateRequest, RoleAssignRequest

router = APIRouter()


@router.get("/operators")
async def list_operators(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadMembers,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """A page of the address book, ordered by email. Filter by `email`."""
    del session
    result = await listing.members(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, MEMBER_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    return {"operators": result.items, "next": result.next}


@router.post("/operators", status_code=201)
async def add_operator(payload: OperatorCreateRequest, principal: PrincipalDep,
                       container: ContainerDep) -> dict[str, Any]:
    """Add an account to the tenant with an explicit set of roles.

    No roles means the default (viewer), so an omission narrows access instead of
    widening it. Delegation may only narrow too: the caller must already hold
    whatever it hands out.
    """
    permits(principal, "members:write")
    caller = principal.caller
    roles = RoleCatalog(container)
    role_ids = await roles.resolve(caller.tenant, payload.role_ids)
    roles.ensure_grantable(
        caller,
        await roles.permissions_of(caller.tenant, role_ids),
        await roles.permissions_of(caller.tenant, DEFAULT_ROLE_IDS),
    )
    operator_id = await container.commands.register_operator(
        email=payload.email,
        password=payload.password,
        is_admin=False,
        role_ids=role_ids,
        tenant_id=caller.tenant,
    )
    return {
        "operator_id": operator_id,
        "tenant_id": caller.tenant,
        "is_admin": OWNER_ROLE in role_ids,
        "role_ids": list(role_ids),
    }


@router.put("/operators/{operator_id}/roles")
async def assign_roles(operator_id: str, payload: RoleAssignRequest,
                       principal: PrincipalDep,
                       container: ContainerDep) -> dict[str, Any]:
    """Replace an account's roles. Takes effect on their next request: the
    session carries no permissions, and they are resolved per request."""
    permits(principal, "roles:write")
    caller = principal.caller
    roles = RoleCatalog(container)
    role_ids = await roles.resolve(caller.tenant, payload.role_ids)
    roles.ensure_grantable(
        caller, await roles.permissions_of(caller.tenant, role_ids)
    )
    await container.commands.assign_roles(
        operator_id, role_ids=role_ids, actor=caller.email
    )
    return {"status": "assigned", "role_ids": list(role_ids)}


@router.post("/operators/{operator_id}/disable")
async def disable_operator(operator_id: str, principal: PrincipalDep,
                           container: ContainerDep) -> dict[str, Any]:
    """Stop an account signing in, and stop its live sessions on the next request.

    Not a delete: the events it wrote are the record of what it did while it had
    access, and rewriting history to make an offboarding look tidier is exactly
    the wrong trade. An account cannot disable itself, which is the one case
    where the operator would lock themselves out with no way back in.
    """
    permits(principal, "members:write")
    if operator_id == principal.session.operator_id:
        raise HTTPException(
            status_code=409, detail="an account cannot disable itself"
        )
    await container.commands.disable_operator(
        operator_id, actor=principal.session.email
    )
    return {"status": "disabled"}

@router.post("/operators/{operator_id}/enable")
async def enable_operator(operator_id: str, principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, Any]:
    """Restore access. The password was never touched, so there is nothing to reset."""
    permits(principal, "members:write")
    await container.commands.enable_operator(
        operator_id, actor=principal.session.email
    )
    return {"status": "enabled"}

__all__ = ["router"]
