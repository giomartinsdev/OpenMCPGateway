"""Gateway CRUD, auth configuration, and the sub-resources under a gateway."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from gateway.application.services import connect, gateways, inspect, listing
from gateway.domain.filters import EVENT_FILTERS, GATEWAY_FILTERS
from gateway.interface.dependencies import (
    CanReadGateways,
    CanReadKeys,
    CanWriteGateways,
    CanWriteKeys,
    ContainerDep,
    PrincipalDep,
)
from gateway.interface.listing import PageQuery, filters
from gateway.interface.schemas import (
    ApiKeyRequest,
    AuthConfigRequest,
    GatewayCreateRequest,
    GatewayToolsRequest,
    GatewayUpdateRequest,
    PrincipalRequest,
    QuotaRequest,
    RateLimitRequest,
    RestoreRequest,
    WebhookRequest,
)

router = APIRouter()


@router.get("/gateways")
async def list_gateways(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadGateways,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """A page of this tenant's gateways.

    Filter by `slug`, `name`, `auth_mode`, `enabled`; sort by `created_at`,
    `updated_at`, `name`, `slug`.
    """
    del session
    result = await listing.gateways(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, GATEWAY_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    # `gateways` and `next` rather than `items`/`next`: the key was there before
    # the cursor was, and a list that only adds fields cannot break a client.
    return {"gateways": result.items, "next": result.next}


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


@router.get("/gateways/{gateway_id}/upstream-tools")
async def upstream_tools(gateway_id: str, session: CanReadGateways,
                         principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, Any]:
    """What the upstream serves, so the allowlist can be ticked rather than typed.

    Read permission, not write: it is a listing of a server the caller can
    already read through this gateway. It does reach the upstream with the
    gateway's credential, which is why it is not public.
    """
    del session
    gateway = await gateways.owned(container, principal.caller, gateway_id)
    return await connect.upstream_tools(container, gateway)


@router.post("/gateways/{gateway_id}/test")
async def test_upstream(gateway_id: str, session: CanWriteGateways,
                        principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    """Probe the upstream now: a real MCP handshake, timed, with the reason.

    Write permission: this reaches a server the operator only indirectly owns and
    costs a round trip, so it is not something a read-only role should be able to
    make the gateway do on demand.
    """
    del session
    gateway = await gateways.owned(container, principal.caller, gateway_id)
    result = await container.prober.probe(container, gateway, fresh=True)
    return result.model_dump(mode="json")


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
        allow_api_key=payload.allow_api_key,
        verifiers=tuple(payload.verifiers),
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


@router.put("/gateways/{gateway_id}/quota")
async def set_quota(gateway_id: str, payload: QuotaRequest,
                    session: CanWriteGateways, principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, int]:
    """A calendar-month ceiling, separate from the per-minute rate limit.

    No router refresh: the quota is read from the read model per request, and
    the guard's cached config only carries the ceiling's *value*, which the
    signature already includes.
    """
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.set_monthly_quota(
        gateway_id, monthly_quota=payload.monthly_quota, actor=session.email
    )
    await container.refresh_router()
    return {"monthly_quota": payload.monthly_quota}

# -- history ---------------------------------------------------------------

@router.get("/gateways/{gateway_id}/history")
async def gateway_history(
    gateway_id: str,
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadGateways, principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """This gateway's own stream, newest first, paged. Secrets are redacted.

    Same filters and sorts as `/events`, narrowed to one aggregate: "why did this
    one change" is a question about a stream, and the audit feed is the wrong
    scope for it.
    """
    del session
    gateway = await gateways.owned(container, principal.caller, gateway_id)
    result = await inspect.history(
        container, gateway,
        window=page.window,
        filters=filters(request, EVENT_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    return {"history": result.items, "next": result.next}

@router.get("/gateways/{gateway_id}/history/{version}")
async def gateway_snapshot(gateway_id: str, version: int,
                           session: CanReadGateways, principal: PrincipalDep,
                           container: ContainerDep) -> dict[str, Any]:
    """The configuration that version left behind, for a preview before restore."""
    del session
    gateway = await gateways.owned(container, principal.caller, gateway_id)
    return await inspect.configuration_at(container, gateway, version)

@router.post("/gateways/{gateway_id}/restore")
async def restore_gateway(gateway_id: str, payload: RestoreRequest,
                          session: CanWriteGateways, principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, Any]:
    """Roll the configuration back. Credentials and keys are not restored."""
    gateway = await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.restore_gateway(
        gateway["id"], version=payload.version, actor=session.email
    )
    await container.refresh_router()
    return {"status": "restored", "version": payload.version}

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


@router.put("/gateways/{gateway_id}/tools")
async def set_tools(gateway_id: str, payload: GatewayToolsRequest,
                    session: CanWriteGateways, principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, Any]:
    """Replace the allowlist of upstream tools this gateway exposes.

    A rebuild, not a restart: the guard and the proxy both read the new list on
    the next request, and the signature change is what triggers it.
    """
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.set_gateway_tools(
        gateway_id, tools=payload.tools, actor=session.email
    )
    await container.refresh_router()
    return {"tools": payload.tools}


@router.post("/gateways/{gateway_id}/keys", status_code=201)
async def issue_key(gateway_id: str, payload: ApiKeyRequest, session: CanWriteKeys,
                    principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    plaintext, key_id = await container.commands.issue_api_key(
        gateway_id, label=payload.label, actor=session.email,
        expires_at=(
            datetime.now(UTC) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
    )
    # Shown once here, and readable once more through the reveal endpoint.
    return {"key_id": key_id, "api_key": plaintext}


@router.post("/gateways/{gateway_id}/keys/{key_id}/reveal")
async def reveal_key(gateway_id: str, key_id: str, session: CanReadKeys,
                     principal: PrincipalDep,
                     container: ContainerDep) -> dict[str, str]:
    """Show the plaintext of a key that has never been revealed, once.

    `keys:read` rather than `keys:write`: reading a credential you were already
    issued should not require the power to mint or revoke one. It is still
    logged — the reveal appends an event naming the operator — and it burns the
    sealed copy, so the second call answers 409 instead of the same secret.

    A key issued before sealing existed has nothing to open, and a deployment
    that rotated `OMG_SESSION_SECRET` cannot open its old keys; both answer 409
    with the fix (rotate) rather than a 500.
    """
    await gateways.owned(container, principal.caller, gateway_id)
    sealed = await container.read_models.sealed_key(gateway_id, key_id)
    if not sealed:
        raise HTTPException(
            status_code=409,
            detail="this key can no longer be shown; rotate it to get a new one",
        )
    plaintext = await container.commands.reveal_api_key(
        gateway_id, key_id=key_id, sealed=sealed, actor=session.email
    )
    if plaintext is None:
        raise HTTPException(
            status_code=409,
            detail="this key can no longer be shown; rotate it to get a new one",
        )
    return {"key_id": key_id, "api_key": plaintext}


@router.post("/gateways/{gateway_id}/keys/{key_id}/rotate")
async def rotate_key(gateway_id: str, key_id: str, session: CanWriteKeys,
                     principal: PrincipalDep,
                     container: ContainerDep) -> dict[str, str]:
    """Issue a replacement and revoke this one in the same append.

    Returns the new plaintext — the only moment it exists outside the caller's
    clipboard.
    """
    await gateways.owned(container, principal.caller, gateway_id)
    plaintext, new_id = await container.commands.rotate_api_key(
        gateway_id, key_id=key_id, actor=session.email
    )
    await container.refresh_router()
    return {"key_id": new_id, "api_key": plaintext}


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


# -- webhooks --------------------------------------------------------------

@router.get("/gateways/{gateway_id}/webhooks")
async def list_webhooks(gateway_id: str, session: CanReadGateways,
                        principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    del session
    await gateways.owned(container, principal.caller, gateway_id)
    rows = await container.read_models.webhooks(gateway_id)
    return {"webhooks": [row.model_dump(mode="json") for row in rows]}

@router.post("/gateways/{gateway_id}/webhooks", status_code=201)
async def register_webhook(gateway_id: str, payload: WebhookRequest,
                           session: CanWriteGateways, principal: PrincipalDep,
                           container: ContainerDep) -> dict[str, Any]:
    """Register a hook. The secret is the caller's; the server keeps it sealed."""
    await gateways.owned(container, principal.caller, gateway_id)
    webhook_id, _secret = await container.commands.register_webhook(
        gateway_id, url=payload.url, secret=payload.secret,
        outcomes=tuple(payload.outcomes), actor=session.email,
    )
    return {"id": webhook_id, "url": payload.url,
            "outcomes": payload.outcomes, "enabled": True}

@router.delete("/gateways/{gateway_id}/webhooks/{webhook_id}")
async def delete_webhook(gateway_id: str, webhook_id: str,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await gateways.owned(container, principal.caller, gateway_id)
    await container.commands.delete_webhook(
        gateway_id, webhook_id=webhook_id, actor=session.email
    )
    return {"status": "deleted"}

@router.get("/gateways/{gateway_id}/webhooks/{webhook_id}/deliveries")
async def webhook_deliveries(gateway_id: str, webhook_id: str,
                             session: CanReadGateways, principal: PrincipalDep,
                             container: ContainerDep) -> dict[str, Any]:
    """"The hook is silent" has two causes; this is how to tell them apart."""
    del session
    await gateways.owned(container, principal.caller, gateway_id)
    rows = await container.read_models.webhook_deliveries(webhook_id)
    return {"deliveries": [row.model_dump(mode="json") for row in rows]}

__all__ = ["router"]
