"""Creating a tenant and joining one — the two ways out of onboarding."""

from typing import Any

from fastapi import APIRouter

from gateway.application.services import tenants
from gateway.interface.dependencies import ContainerDep, PrincipalDep
from gateway.interface.schemas import TenantCreateRequest, TenantJoinRequest

router = APIRouter()


@router.post("/tenants", status_code=201)
async def create_tenant(payload: TenantCreateRequest, principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    return await tenants.create(container, principal.caller, payload.name)


@router.post("/tenants/join")
async def join_tenant(payload: TenantJoinRequest, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, Any]:
    return await tenants.join(container, principal.caller, payload.code)


@router.get("/tenants/current")
async def current_tenant(principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, Any]:
    return await tenants.current(container, principal.caller)


__all__ = ["router"]
