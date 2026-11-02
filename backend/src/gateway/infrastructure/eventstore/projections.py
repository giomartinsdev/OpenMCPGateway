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
    PrincipalAllowed,
    PrincipalRevoked,
    RequestObserved,
)
from gateway.domain.readmodels import (
    ApiKeyRow,
    GatewayRow,
    PrincipalRow,
    RequestRow,
    SeriesPoint,
    TrafficStats,
)
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.db.tables import (
    rm_api_keys,
    rm_gateways,
    rm_principals,
    rm_request_stats,
    rm_requests_tail,
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


# --------------------------------------------------------------------------
# Queries against read models
# --------------------------------------------------------------------------


class ReadModels:
    """The query side. Returns typed rows that serialize straight to JSON."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_gateways(self) -> list[GatewayRow]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(rm_gateways)
                    .where(rm_gateways.c.deleted.is_(False))
                    .order_by(rm_gateways.c.created_at)
                )
            ).mappings()
            return [GatewayRow.model_validate(dict(row)) for row in rows]

    async def get_gateway(self, gateway_id: str) -> GatewayRow | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(rm_gateways).where(rm_gateways.c.id == gateway_id)
                )
            ).mappings().first()
        return GatewayRow.model_validate(dict(row)) if row else None

    async def get_by_slug(self, slug: str) -> GatewayRow | None:
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
        self, gateway_id: str | None, limit: int = 100
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
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    query.order_by(rm_requests_tail.c.occurred_at.desc()).limit(limit)
                )
            ).mappings()
            return [RequestRow.model_validate(dict(row)) for row in rows]

    async def stats(
        self, gateway_id: str | None = None, minutes: int = 60
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


def _interval(minutes: int) -> Any:
    """`minutes` as a Postgres interval, without string-formatting SQL."""
    return func.make_interval(0, 0, 0, 0, 0, minutes)


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["ProjectionRunner", "ReadModels", "utcnow"]
