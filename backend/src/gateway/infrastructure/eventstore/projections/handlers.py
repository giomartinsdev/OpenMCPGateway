"""Event -> read model. One handler per event type.

Handlers are registered by event type rather than by an `isinstance` ladder: the
runner stays open for new events and closed for modification, and a missing
handler is an explicit "this event feeds no read model" rather than a silent
no-op.

Two rules keep this consistent:
  * a projection is derived state — it can always be rebuilt from `events`
  * updates are idempotent upserts keyed on the aggregate id, so replaying an
    existing checkpoint is harmless
"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.domain.events import (
    ApiKeyIssued,
    ApiKeyRevealed,
    ApiKeyRevoked,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayQuotaSet,
    GatewayRateLimitSet,
    GatewayRestored,
    GatewayToolsConfigured,
    GatewayUpdated,
    OperatorDisabled,
    OperatorEnabled,
    OperatorJoinedTenant,
    OperatorRegistered,
    OperatorRolesAssigned,
    PrincipalAllowed,
    PrincipalRevoked,
    RequestObserved,
    RoleDefined,
    TenantCreated,
    WebhookDeleted,
    WebhookRegistered,
)
from gateway.infrastructure.db.tables import (
    rm_api_keys,
    rm_gateways,
    rm_operators,
    rm_principals,
    rm_request_stats,
    rm_requests_tail,
    rm_roles,
    rm_tenants,
    rm_webhooks,
)

Handler = Callable[[AsyncSession, Event], Awaitable[None]]

def _enum_value(value: Any) -> str | None:
    """Enum member or plain string -> the stored string, None-safe."""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


# -- handlers -------------------------------------------------------------
#
# One session per event keeps replays and live writes using the same path. A
# batch would be faster at boot and is not worth a second code path.


async def on_gateway_created(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayCreated)
    await session.execute(
        pg_insert(rm_gateways)
        .values(
            id=event.aggregate_id,
            tenant_id=event.tenant_id,
            slug=event.slug,
            name=event.name,
            target_url=event.target_url,
            description=event.description,
            auth_mode=_enum_value(event.auth_mode),
            idp=_enum_value(event.idp),
            created_at=event.occurred_at,
            updated_at=event.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[rm_gateways.c.id])
    )


async def on_gateway_updated(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayUpdated)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(
            name=func.coalesce(event.name, rm_gateways.c.name),
            target_url=func.coalesce(event.target_url, rm_gateways.c.target_url),
            description=func.coalesce(event.description, rm_gateways.c.description),
            updated_at=func.now(),
        )
    )


async def on_auth_configured(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayAuthConfigured)
    # client_secret deliberately never copied into the read model.
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(
            auth_mode=_enum_value(event.auth_mode),
            idp=_enum_value(event.idp),
            client_id=event.client_id,
            config_url=event.config_url,
            required_scopes=list(event.required_scopes),
            updated_at=func.now(),
        )
    )


async def on_rate_limit(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayRateLimitSet)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(
            requests_per_minute=event.requests_per_minute, updated_at=func.now()
        )
    )


async def on_quota_set(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayQuotaSet)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(monthly_quota=event.monthly_quota, updated_at=func.now())
    )

async def on_restored(session: AsyncSession, event: Event) -> None:
    """Write the restored fields, and nothing about keys or credentials.

    The event deliberately does not carry them; this handler must not invent
    them either, or a rollback would quietly undo a revocation.
    """
    assert isinstance(event, GatewayRestored)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(
            name=event.name,
            target_url=event.target_url,
            description=event.description,
            requests_per_minute=event.requests_per_minute,
            tools=list(event.tools),
            updated_at=func.now(),
        )
    )

async def on_operator_disabled(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorDisabled)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(disabled=True)
    )

async def on_operator_enabled(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorEnabled)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(disabled=False)
    )

async def on_disabled(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(enabled=False, updated_at=func.now())
    )


async def on_enabled(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(enabled=True, updated_at=func.now())
    )


async def on_deleted(session: AsyncSession, event: Event) -> None:
    # Slug is kept for the audit trail; the partial unique index only covers
    # live rows, so the name is free to reuse.
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(deleted=True, enabled=False, updated_at=func.now())
    )


async def on_principal_allowed(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, PrincipalAllowed)
    statement = pg_insert(rm_principals).values(
        gateway_id=event.aggregate_id,
        subject=event.subject,
        note=event.note,
        added_at=event.occurred_at,
        revoked=False,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_principals.c.gateway_id, rm_principals.c.subject],
            set_={"revoked": False, "note": statement.excluded.note},
        )
    )


async def on_principal_revoked(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, PrincipalRevoked)
    await session.execute(
        update(rm_principals)
        .where(
            (rm_principals.c.gateway_id == event.aggregate_id)
            & (rm_principals.c.subject == event.subject)
        )
        .values(revoked=True)
    )


async def on_key_issued(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, ApiKeyIssued)
    statement = pg_insert(rm_api_keys).values(
        gateway_id=event.aggregate_id,
        key_id=event.key_id,
        label=event.label,
        key_hash=event.key_hash,
        subject=event.subject,
        sealed=event.sealed,
        issued_at=event.occurred_at,
        expires_at=event.expires_at,
        revoked=False,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_api_keys.c.gateway_id, rm_api_keys.c.key_id],
            set_={"revoked": False},
        )
    )


async def on_key_revealed(session: AsyncSession, event: Event) -> None:
    """Record the reveal and burn the sealed copy in the same statement.

    Wiping here rather than in the command keeps the read model the thing that
    decides what is still readable: after this, the plaintext exists only in the
    operator's clipboard. History keeps the event, not the secret.
    """
    assert isinstance(event, ApiKeyRevealed)
    await session.execute(
        update(rm_api_keys)
        .where(
            (rm_api_keys.c.gateway_id == event.aggregate_id)
            & (rm_api_keys.c.key_id == event.key_id)
        )
        .values(sealed="", revealed_at=event.occurred_at, revealed_by=event.actor)
    )


async def on_tools_configured(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayToolsConfigured)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(tools=list(event.tools), updated_at=func.now())
    )


async def on_key_revoked(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, ApiKeyRevoked)
    await session.execute(
        update(rm_api_keys)
        .where(
            (rm_api_keys.c.gateway_id == event.aggregate_id)
            & (rm_api_keys.c.key_id == event.key_id)
        )
        .values(revoked=True)
    )


async def on_request(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, RequestObserved)
    bucket = event.occurred_at.replace(second=0, microsecond=0)
    await session.execute(
        pg_insert(rm_requests_tail)
        .values(
            event_id=event.event_id,
            gateway_id=event.gateway_id,
            subject=event.subject,
            outcome=event.outcome,
            status=event.status,
            duration_ms=event.duration_ms,
            ip=event.ip,
            tool=event.tool,
            method=event.method,
            reason=event.reason,
            request_id=event.request_id,
            occurred_at=event.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[rm_requests_tail.c.event_id])
    )

    statement = pg_insert(rm_request_stats).values(
        gateway_id=event.gateway_id,
        bucket=bucket,
        outcome=event.outcome,
        ip=event.ip or "",
        count=1,
        total_ms=event.duration_ms,
        max_ms=event.duration_ms,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                rm_request_stats.c.gateway_id,
                rm_request_stats.c.bucket,
                rm_request_stats.c.outcome,
                rm_request_stats.c.ip,
            ],
            set_={
                "count": rm_request_stats.c.count + 1,
                "total_ms": rm_request_stats.c.total_ms + statement.excluded.total_ms,
                "max_ms": func.greatest(
                    rm_request_stats.c.max_ms, statement.excluded.max_ms
                ),
            },
        )
    )


async def on_operator_registered(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorRegistered)
    statement = pg_insert(rm_operators).values(
        id=event.aggregate_id,
        email=event.email,
        tenant_id=event.tenant_id,
        role_ids=list(event.role_ids),
        is_admin=event.is_admin,
        created_at=event.occurred_at,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_operators.c.id],
            set_={
                "email": statement.excluded.email,
                "role_ids": statement.excluded.role_ids,
                "is_admin": statement.excluded.is_admin,
            },
        )
    )

async def on_joined_tenant(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorJoinedTenant)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(tenant_id=event.tenant_id, role_ids=list(event.role_ids))
    )

async def on_tenant_created(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, TenantCreated)
    await session.execute(
        pg_insert(rm_tenants).values(
            id=event.aggregate_id,
            name=event.name,
            join_code=event.join_code,
            created_at=event.occurred_at,
        ).on_conflict_do_nothing(index_elements=[rm_tenants.c.id])
    )


async def on_roles_assigned(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorRolesAssigned)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(role_ids=list(event.role_ids))
    )


async def on_role_defined(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, RoleDefined)
    statement = pg_insert(rm_roles).values(
        id=event.aggregate_id,
        tenant_id=event.tenant_id,
        name=event.name,
        permissions=list(event.permissions),
        deleted=False,
        created_at=event.occurred_at,
        updated_at=event.occurred_at,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_roles.c.id],
            set_={
                "name": statement.excluded.name,
                "permissions": statement.excluded.permissions,
                "deleted": False,
                "updated_at": func.now(),
            },
        )
    )


async def on_role_deleted(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_roles)
        .where(rm_roles.c.id == event.aggregate_id)
        .values(deleted=True, updated_at=func.now())
    )


async def on_webhook_registered(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, WebhookRegistered)
    statement = pg_insert(rm_webhooks).values(
        id=event.webhook_id,
        gateway_id=event.aggregate_id,
        url=event.url,
        secret=event.secret,
        outcomes=list(event.outcomes),
        enabled=True,
        created_at=event.occurred_at,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_webhooks.c.id],
            set_={"url": statement.excluded.url, "enabled": True},
        )
    )

async def on_webhook_deleted(session: AsyncSession, event: Event) -> None:
    """Keep the row, switch it off: a delivery history that outlives its hook is
    the only evidence of what the hook was told."""
    assert isinstance(event, WebhookDeleted)
    await session.execute(
        update(rm_webhooks)
        .where(rm_webhooks.c.id == event.webhook_id)
        .values(enabled=False)
    )
