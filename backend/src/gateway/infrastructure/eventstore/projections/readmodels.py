"""The query side: typed rows that serialize straight to JSON.

Read models exist because the write model is too chatty for a dashboard and
cannot aggregate across gateways. Every query here is a single round trip; the
proxy hot path and the panel both go through this class so there is exactly one
statement of "what the current state is".
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from gateway.domain.events import OperatorRegistered
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


__all__ = ["ReadModels", "utcnow"]
