"""Projections: fold the event log into query-optimised read models.

The write model (aggregates) answers "may I?" but is too chatty for a dashboard
and cannot aggregate across gateways. Projections exist so the API and the
proxy hot path never replay history.

Two rules keep this consistent:
  * a projection is derived state — it can always be rebuilt from `events`
  * updates are idempotent upserts keyed on the aggregate id, so replaying an
    existing checkpoint is harmless

Handlers are registered by event type rather than by `isinstance` ladder: the
runner is open for new events and closed for modification, and a missing handler
is an explicit "this event feeds no read model" rather than a silent no-op.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.domain.events import (
    ApiKeyIssued,
    ApiKeyRevoked,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayRateLimitSet,
    GatewayUpdated,
    OperatorJoinedTenant,
    OperatorRegistered,
    OperatorRolesAssigned,
    PrincipalAllowed,
    PrincipalRevoked,
    RequestObserved,
    RoleDefined,
    RoleDeleted,
    TenantCreated,
)
from gateway.domain.permissions import DEFAULT_ROLE_IDS, OWNER_ROLE
from gateway.domain.readmodels import (
    ApiKeyRow,
    GatewayRow,
    OperatorRow,
    PrincipalRow,
    RequestRow,
    RoleRow,
    SeriesPoint,
    TenantRow,
    TrafficStats,
)
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.db.tables import (
    events,
    rm_api_keys,
    rm_gateways,
    rm_operators,
    rm_principals,
    rm_request_stats,
    rm_requests_tail,
    rm_roles,
    rm_tenants,
)

Handler = Callable[[AsyncSession, Event], Awaitable[None]]


def _enum_value(value: Any) -> str | None:
    """Enum member or plain string -> the stored string, None-safe."""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


class ProjectionRunner:
    """Applies events to read models, tracking its own checkpoint."""

    name = "read_models"

    def __init__(self, database: Database) -> None:
        self._db = database
        self._handlers: dict[str, Handler] = {
            GatewayCreated.EVENT: _on_gateway_created,
            GatewayUpdated.EVENT: _on_gateway_updated,
            GatewayAuthConfigured.EVENT: _on_auth_configured,
            GatewayRateLimitSet.EVENT: _on_rate_limit,
            GatewayDisabled.EVENT: _on_disabled,
            GatewayEnabled.EVENT: _on_enabled,
            GatewayDeleted.EVENT: _on_deleted,
            PrincipalAllowed.EVENT: _on_principal_allowed,
            PrincipalRevoked.EVENT: _on_principal_revoked,
            ApiKeyIssued.EVENT: _on_key_issued,
            ApiKeyRevoked.EVENT: _on_key_revoked,
            RequestObserved.EVENT: _on_request,
            OperatorRegistered.EVENT: _on_operator_registered,
            OperatorRolesAssigned.EVENT: _on_roles_assigned,
            OperatorJoinedTenant.EVENT: _on_joined_tenant,
            TenantCreated.EVENT: _on_tenant_created,
            RoleDefined.EVENT: _on_role_defined,
            RoleDeleted.EVENT: _on_role_deleted,
        }

    async def apply(self, global_seq: int, event: Event) -> None:
        del global_seq  # Ordering is the log's job; handlers are idempotent.
        handler = self._handlers.get(event.EVENT)
        if handler is None:
            return
        async with self._db.session() as session, session.begin():
            await handler(session, event)


# -- handlers -------------------------------------------------------------
#
# One session per event keeps replays and live writes using the same path. A
# batch would be faster at boot and is not worth a second code path.


async def _on_gateway_created(session: AsyncSession, event: Event) -> None:
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


async def _on_gateway_updated(session: AsyncSession, event: Event) -> None:
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


async def _on_auth_configured(session: AsyncSession, event: Event) -> None:
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


async def _on_rate_limit(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, GatewayRateLimitSet)
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(
            requests_per_minute=event.requests_per_minute, updated_at=func.now()
        )
    )


async def _on_disabled(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(enabled=False, updated_at=func.now())
    )


async def _on_enabled(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(enabled=True, updated_at=func.now())
    )


async def _on_deleted(session: AsyncSession, event: Event) -> None:
    # Slug is kept for the audit trail; the partial unique index only covers
    # live rows, so the name is free to reuse.
    await session.execute(
        update(rm_gateways)
        .where(rm_gateways.c.id == event.aggregate_id)
        .values(deleted=True, enabled=False, updated_at=func.now())
    )


async def _on_principal_allowed(session: AsyncSession, event: Event) -> None:
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


async def _on_principal_revoked(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, PrincipalRevoked)
    await session.execute(
        update(rm_principals)
        .where(
            (rm_principals.c.gateway_id == event.aggregate_id)
            & (rm_principals.c.subject == event.subject)
        )
        .values(revoked=True)
    )


async def _on_key_issued(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, ApiKeyIssued)
    statement = pg_insert(rm_api_keys).values(
        gateway_id=event.aggregate_id,
        key_id=event.key_id,
        label=event.label,
        key_hash=event.key_hash,
        subject=event.subject,
        issued_at=event.occurred_at,
        revoked=False,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[rm_api_keys.c.gateway_id, rm_api_keys.c.key_id],
            set_={"revoked": False},
        )
    )


async def _on_key_revoked(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, ApiKeyRevoked)
    await session.execute(
        update(rm_api_keys)
        .where(
            (rm_api_keys.c.gateway_id == event.aggregate_id)
            & (rm_api_keys.c.key_id == event.key_id)
        )
        .values(revoked=True)
    )


async def _on_request(session: AsyncSession, event: Event) -> None:
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


async def _on_operator_registered(session: AsyncSession, event: Event) -> None:
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

async def _on_joined_tenant(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorJoinedTenant)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(tenant_id=event.tenant_id, role_ids=list(event.role_ids))
    )

async def _on_tenant_created(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, TenantCreated)
    await session.execute(
        pg_insert(rm_tenants).values(
            id=event.aggregate_id,
            name=event.name,
            join_code=event.join_code,
            created_at=event.occurred_at,
        ).on_conflict_do_nothing(index_elements=[rm_tenants.c.id])
    )


async def _on_roles_assigned(session: AsyncSession, event: Event) -> None:
    assert isinstance(event, OperatorRolesAssigned)
    await session.execute(
        update(rm_operators)
        .where(rm_operators.c.id == event.aggregate_id)
        .values(role_ids=list(event.role_ids))
    )


async def _on_role_defined(session: AsyncSession, event: Event) -> None:
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


async def _on_role_deleted(session: AsyncSession, event: Event) -> None:
    await session.execute(
        update(rm_roles)
        .where(rm_roles.c.id == event.aggregate_id)
        .values(deleted=True, updated_at=func.now())
    )


# --------------------------------------------------------------------------
# Queries against read models
# --------------------------------------------------------------------------

class ReadModels:
    """The query side. Returns typed rows that serialize straight to JSON."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_gateways(self, tenant_id: str) -> list[GatewayRow]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_gateways)
                    .where(
                        (rm_gateways.c.tenant_id == tenant_id)
                        & rm_gateways.c.deleted.is_(False)
                    )
                    .order_by(rm_gateways.c.created_at)
                )
            ).mappings()
            return [GatewayRow.model_validate(dict(row)) for row in rows]

    async def all_gateways(self) -> list[GatewayRow]:
        """Every live gateway, whoever owns it — the proxy's view.

        The proxy serves by public slug, and a slug is global, so it cannot ask
        "for this tenant". Named for what it is: an unscoped read is a deliberate
        call site, not an omission.
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_gateways)
                    .where(rm_gateways.c.deleted.is_(False))
                    .order_by(rm_gateways.c.created_at)
                )
            ).mappings()
            return [GatewayRow.model_validate(dict(row)) for row in rows]

    async def get_gateway(
        self, gateway_id: str, tenant_id: str
    ) -> GatewayRow | None:
        """Scoped by tenant in the query, not checked afterwards.

        A caller that forgot a filter would otherwise read another tenant's
        gateway; here the filter is what makes the row exist at all.
        """
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_gateways).where(
                        (rm_gateways.c.id == gateway_id)
                        & (rm_gateways.c.tenant_id == tenant_id)
                    )
                )
            ).mappings().first()
        return GatewayRow.model_validate(dict(row)) if row else None

    async def get_by_slug(self, slug: str) -> GatewayRow | None:
        """Unscoped on purpose: this is the proxy's public lookup, where the slug
        in the URL is the only input. `tenant_id` rides along for the router."""
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_gateways).where(
                        (rm_gateways.c.slug == slug)
                        & rm_gateways.c.deleted.is_(False)
                    )
                )
            ).mappings().first()
        return GatewayRow.model_validate(dict(row)) if row else None

    async def tenant(self, tenant_id: str) -> TenantRow | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_tenants).where(rm_tenants.c.id == tenant_id)
                )
            ).mappings().first()
        return TenantRow.model_validate(dict(row)) if row else None

    async def tenant_by_code(self, join_code: str) -> TenantRow | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_tenants).where(
                        func.upper(rm_tenants.c.join_code) == join_code.strip().upper()
                    )
                )
            ).mappings().first()
        return TenantRow.model_validate(dict(row)) if row else None

    async def join_code_taken(self, join_code: str) -> bool:
        async with self._db.session() as session:
            return (
                await session.scalar(
                    select(rm_tenants.c.id).where(
                        func.upper(rm_tenants.c.join_code) == join_code.upper()
                    )
                )
            ) is not None

    async def principals(self, gateway_id: str) -> list[PrincipalRow]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        rm_principals.c.subject,
                        rm_principals.c.note,
                        rm_principals.c.added_at,
                    )
                    .where(
                        (rm_principals.c.gateway_id == gateway_id)
                        & rm_principals.c.revoked.is_(False)
                    )
                    .order_by(rm_principals.c.added_at)
                )
            ).mappings()
            return [PrincipalRow.model_validate(dict(row)) for row in rows]

    async def api_keys(self, gateway_id: str) -> list[ApiKeyRow]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        rm_api_keys.c.key_id,
                        rm_api_keys.c.label,
                        rm_api_keys.c.subject,
                        rm_api_keys.c.issued_at,
                    )
                    .where(
                        (rm_api_keys.c.gateway_id == gateway_id)
                        & rm_api_keys.c.revoked.is_(False)
                    )
                    .order_by(rm_api_keys.c.issued_at)
                )
            ).mappings()
            return [ApiKeyRow.model_validate(dict(row)) for row in rows]

    async def subject_for_key_hash(
        self, gateway_id: str, key_hash: str
    ) -> str | None:
        async with self._db.session() as session:
            return await session.scalar(
                select(rm_api_keys.c.subject).where(
                    (rm_api_keys.c.gateway_id == gateway_id)
                    & (rm_api_keys.c.key_hash == key_hash)
                    & rm_api_keys.c.revoked.is_(False)
                )
            )

    async def request_tail(
        self, gateway_id: str | None, limit: int = 100,
        *, tenant_id: str | None = None,
    ) -> list[RequestRow]:
        query = select(
            rm_requests_tail.c.gateway_id,
            rm_requests_tail.c.subject,
            rm_requests_tail.c.outcome,
            rm_requests_tail.c.status,
            rm_requests_tail.c.duration_ms,
            rm_requests_tail.c.ip,
            rm_requests_tail.c.tool,
            rm_requests_tail.c.method,
            rm_requests_tail.c.occurred_at,
        )
        if gateway_id is not None:
            query = query.where(rm_requests_tail.c.gateway_id == gateway_id)
        if tenant_id is not None:
            query = query.where(
                rm_requests_tail.c.gateway_id.in_(_tenant_gateways(tenant_id))
            )
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    query.order_by(rm_requests_tail.c.occurred_at.desc()).limit(limit)
                )
            ).mappings()
            return [RequestRow.model_validate(dict(row)) for row in rows]

    async def list_roles(self, tenant_id: str) -> list[RoleRow]:
        """This tenant's custom roles. Built-ins are added by the API layer."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_roles)
                    .where(
                        (rm_roles.c.tenant_id == tenant_id)
                        & rm_roles.c.deleted.is_(False)
                    )
                    .order_by(rm_roles.c.name)
                )
            ).mappings()
            return [RoleRow.model_validate(dict(row)) for row in rows]

    async def role_permissions(
        self, tenant_id: str
    ) -> dict[str, tuple[str, ...]]:
        """Role id -> permissions, for permission resolution. Deleted roles are
        excluded, so deleting a role genuinely revokes it."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_roles.c.id, rm_roles.c.permissions).where(
                        (rm_roles.c.tenant_id == tenant_id)
                        & rm_roles.c.deleted.is_(False)
                    )
                )
            ).all()
        return {role_id: tuple(perms or ()) for role_id, perms in rows}

    async def role_holder_counts(self, tenant_id: str) -> dict[str, int]:
        """Holders per role id, in one read. The panel lists every role at once."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_operators.c.role_ids, rm_operators.c.is_admin).where(
                        rm_operators.c.tenant_id == tenant_id
                    )
                )
            ).all()
        counts: dict[str, int] = {}
        for role_ids, is_admin in rows:
            effective = tuple(role_ids or ()) or (
                (OWNER_ROLE,) if is_admin else DEFAULT_ROLE_IDS
            )
            for role_id in effective:
                counts[role_id] = counts.get(role_id, 0) + 1
        return counts

    async def role_holder_count(self, role_id: str, tenant_id: str) -> int:
        """How many live accounts hold this role. Blocks deleting a role in use."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_operators.c.role_ids, rm_operators.c.is_admin).where(
                        rm_operators.c.tenant_id == tenant_id
                    )
                )
            ).all()
        count = 0
        for role_ids, is_admin in rows:
            effective = tuple(role_ids or ()) or (
                (OWNER_ROLE,) if is_admin else DEFAULT_ROLE_IDS
            )
            count += role_id in effective
        return count

    async def list_operators(self, tenant_id: str) -> list[OperatorRow]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        rm_operators.c.id,
                        rm_operators.c.email,
                        rm_operators.c.tenant_id,
                        rm_operators.c.role_ids,
                        rm_operators.c.is_admin,
                        rm_operators.c.created_at,
                    )
                    .where(rm_operators.c.tenant_id == tenant_id)
                    .order_by(rm_operators.c.created_at)
                )
            ).mappings()
            return [
                OperatorRow(
                    operator_id=row["id"],
                    email=row["email"],
                    tenant_id=row["tenant_id"],
                    is_admin=row["is_admin"],
                    role_ids=tuple(row["role_ids"] or ())
                    or ((OWNER_ROLE,) if row["is_admin"] else DEFAULT_ROLE_IDS),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    async def backfill_legacy_operators(self, tenant_id: str) -> int:
        """Put pre-tenancy accounts into `tenant_id`. Returns how many moved.

        "Pre-tenancy" is a fact in the log, not a guess: events written before
        the field existed have no `tenant_id` key at all, while a new signup
        stores it as `null`. So key existence — not a timestamp, not a boot
        marker — is what separates them, and running this on every boot is
        therefore safe: an account that deliberately has no tenant keeps having
        none.
        """
        legacy = (
            select(events.c.aggregate_id)
            .where(
                (events.c.event_type == OperatorRegistered.EVENT)
                & ~events.c.payload.has_key("tenant_id")
            )
        )
        async with self._db.session() as session, session.begin():
            result = await session.execute(
                update(rm_operators)
                .where(
                    rm_operators.c.tenant_id.is_(None)
                    & rm_operators.c.id.in_(legacy)
                )
                .values(tenant_id=tenant_id)
            )
        return result.rowcount or 0

    async def operator_tenant(self, operator_id: str) -> str | None:
        """The tenant an account belongs to, read fresh.

        Not from the cookie: a member who joined after signing in must not keep
        being told they have no tenant, and a session is a claim about the past.
        """
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_operators.c.tenant_id).where(
                        rm_operators.c.id == operator_id
                    )
                )
            ).first()
        return row[0] if row else None

    async def operator_roles(self, operator_id: str) -> tuple[str, ...] | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_operators.c.role_ids, rm_operators.c.is_admin).where(
                        rm_operators.c.id == operator_id
                    )
                )
            ).first()
        if row is None:
            return None
        role_ids, is_admin = row
        return tuple(role_ids or ()) or ((OWNER_ROLE,) if is_admin else DEFAULT_ROLE_IDS)

    async def stats(
        self, gateway_id: str | None = None, minutes: int = 60,
        *, tenant_id: str | None = None,
    ) -> TrafficStats:
        """Aggregate traffic: totals, per-outcome, per-IP, latency.

        Aggregated in Python rather than SQL because the window is at most a few
        thousand rows (one per gateway/outcome/ip/minute) and doing it here keeps
        the shape of the response easy to change.
        """
        query = select(
            rm_request_stats.c.gateway_id,
            rm_request_stats.c.outcome,
            rm_request_stats.c.ip,
            rm_request_stats.c.count,
            rm_request_stats.c.total_ms,
            rm_request_stats.c.max_ms,
            rm_request_stats.c.bucket,
        ).where(rm_request_stats.c.bucket >= func.now() - _interval(minutes))
        if gateway_id is not None:
            query = query.where(rm_request_stats.c.gateway_id == gateway_id)
        if tenant_id is not None:
            query = query.where(
                rm_request_stats.c.gateway_id.in_(_tenant_gateways(tenant_id))
            )
        async with self._db.session() as session:
            rows = (await session.execute(query)).mappings()

            total = 0
            total_ms = 0
            peak_ms = 0
            by_outcome: dict[str, int] = {}
            by_ip: dict[str, int] = {}
            by_gateway: dict[str, int] = {}
            series: dict[str, int] = {}
            for row in rows:
                count = int(row["count"])
                total += count
                total_ms += int(row["total_ms"])
                peak_ms = max(peak_ms, int(row["max_ms"]))
                by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + count
                ip = row["ip"] or "unknown"
                by_ip[ip] = by_ip.get(ip, 0) + count
                gateway = row["gateway_id"]
                by_gateway[gateway] = by_gateway.get(gateway, 0) + count
                bucket = row["bucket"].isoformat()
                series[bucket] = series.get(bucket, 0) + count

        return TrafficStats(
            window_minutes=minutes,
            requests=total,
            avg_duration_ms=round(total_ms / total, 1) if total else 0,
            max_duration_ms=peak_ms,
            by_outcome=by_outcome,
            by_ip=dict(sorted(by_ip.items(), key=lambda kv: -kv[1])[:20]),
            by_gateway=by_gateway,
            series=[
                SeriesPoint(bucket=bucket, count=count)
                for bucket, count in sorted(series.items())
            ],
        )


def _tenant_gateways(tenant_id: str) -> Any:
    """Gateway ids owned by a tenant, as a subquery.

    Traffic rows key on the gateway, not the tenant, so the filter is a join in
    disguise: it keeps one query per dashboard instead of a read of the gateway
    list followed by an `IN (...)` built in Python.
    """
    return select(rm_gateways.c.id).where(rm_gateways.c.tenant_id == tenant_id)


def _interval(minutes: int) -> Any:
    """`minutes` as a Postgres interval, without string-formatting SQL."""
    return func.make_interval(0, 0, 0, 0, 0, minutes)


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["ProjectionRunner", "ReadModels", "utcnow"]
