"""Gateway CRUD, auth configuration, and the sub-resources under a gateway."""

from typing import Any

from fastapi import APIRouter

from gateway.application.services import connect, gateways
from gateway.interface.dependencies import (
    CanReadGateways,
    CanReadKeys,
    CanWriteGateways,
    CanWriteKeys,
    ContainerDep,
    PrincipalDep,
)
from gateway.interface.schemas import (
    ApiKeyRequest,
    AuthConfigRequest,
    GatewayCreateRequest,
    GatewayUpdateRequest,
    PrincipalRequest,
    RateLimitRequest,
)

router = APIRouter()


@router.get("/gateways")
async def list_gateways(session: CanReadGateways, principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    del session
    return {"gateways": await gateways.list_(container, principal.caller)}


@router.post("/gateways", status_code=201)
async def create_gateway(payload: GatewayCreateRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    gateway_id = await container.commands.create_gateway(
        tenant_id=principal.tenant,
        slug=payload.slug,
        name=payload.name,
        target_url=payload.target_url,
        description=payload.description,
        auth_mode=payload.auth_mode,
        idp=payload.idp,
        actor=session.email,
    )
    await container.refresh_router()
    return {"id": gateway_id}


@router.get("/gateways/{gateway_id}")
async def get_gateway(gateway_id: str, session: CanReadGateways,
                      principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, Any]:
    del session
    return await gateways.detail(container, principal.caller, gateway_id)


@router.get("/gateways/{gateway_id}/connect")
async def connect_guide(gateway_id: str, session: CanReadGateways,
                        principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    """Runnable `curl` and Python for this gateway, token flow included.

    Read-only: it renders what the proxy already does, and never a secret — the
    key placeholder is for the operator to fill in.
    """
    del session
    return await connect.guide(
        container, await gateways.owned(container, principal.caller, gateway_id)
    )


@router.patch("/gateways/{gateway_id}")
async def update_gateway(gateway_id: str, payload: GatewayUpdateRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await container.commands.update_gateway(
        (await gateways.owned(container, principal.caller, gateway_id))["id"],
        name=payload.name,
        target_url=payload.target_url,
        description=payload.description,
        actor=session.email,
    )
    await container.refresh_router()
    return {"status": "updated"}


@router.delete("/gateways/{gateway_id}")
async def delete_gateway(gateway_id: str, session: CanWriteGateways,
                         principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await container.commands.delete_gateway(
        (await gateways.owned(container, principal.caller, gateway_id))["id"],
        actor=session.email,
    )
    await container.refresh_router()
    return {"status": "deleted"}


@router.put("/gateways/{gateway_id}/auth")
async def configure_auth(gateway_id: str, payload: AuthConfigRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.configure_auth(
        gateway_id,
        auth_mode=payload.auth_mode,
        idp=payload.idp,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        config_url=payload.config_url,
        required_scopes=tuple(payload.required_scopes),
        upstream_token=payload.upstream_token,
        actor=session.email,
    )
    await container.refresh_router()
    return {"status": "configured"}


@router.put("/gateways/{gateway_id}/rate-limit")
async def set_rate_limit(gateway_id: str, payload: RateLimitRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.set_rate_limit(
        gateway_id, requests_per_minute=payload.requests_per_minute,
        actor=session.email,
    )
    return {"status": "configured"}


@router.post("/gateways/{gateway_id}/disable")
async def disable_gateway(gateway_id: str, session: CanWriteGateways,
                          principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.disable_gateway(gateway_id, actor=session.email)
    await container.refresh_router()
    return {"status": "disabled"}


@router.post("/gateways/{gateway_id}/enable")
async def enable_gateway(gateway_id: str, session: CanWriteGateways,
                         principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.enable_gateway(gateway_id, actor=session.email)
    await container.refresh_router()
    return {"status": "enabled"}


# -- principals ------------------------------------------------------------


@router.get("/gateways/{gateway_id}/principals")
async def list_principals(gateway_id: str, session: CanReadGateways,
                          principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, Any]:
    del session
    await gateways.owned(container, principal.caller, gateway_id)
    return {"principals": await container.read_models.principals(gateway_id)}


@router.post("/gateways/{gateway_id}/principals", status_code=201)
async def allow_principal(gateway_id: str, payload: PrincipalRequest,
                          session: CanWriteGateways, principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.allow_principal(
        gateway_id, subject=payload.subject, note=payload.note, actor=session.email
    )
    await container.refresh_router()
    return {"status": "allowed"}


@router.delete("/gateways/{gateway_id}/principals/{subject}")
async def revoke_principal(gateway_id: str, subject: str, session: CanWriteGateways,
                           principal: PrincipalDep,
                           container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.revoke_principal(
        gateway_id, subject=subject, actor=session.email
    )
    await container.refresh_router()
    return {"status": "revoked"}


# -- api keys --------------------------------------------------------------


@router.get("/gateways/{gateway_id}/keys")
async def list_keys(gateway_id: str, session: CanReadKeys,
                    principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, Any]:
    del session
    await gateways.owned(container, principal.caller, gateway_id)
    return {"keys": await container.read_models.api_keys(gateway_id)}


@router.post("/gateways/{gateway_id}/keys", status_code=201)
async def issue_key(gateway_id: str, payload: ApiKeyRequest, session: CanWriteKeys,
                    principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    plaintext, key_id = await container.commands.issue_api_key(
        gateway_id, label=payload.label, actor=session.email
    )
    # Shown once. Not stored, not recoverable.
    return {"key_id": key_id, "api_key": plaintext}


@router.delete("/gateways/{gateway_id}/keys/{key_id}")
async def revoke_key(gateway_id: str, key_id: str,
                     session: CanWriteKeys, principal: PrincipalDep,
                     container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.revoke_api_key(
        gateway_id, key_id=key_id, actor=session.email
    )
    await container.refresh_router()
    return {"status": "revoked"}


__all__ = ["router"]
