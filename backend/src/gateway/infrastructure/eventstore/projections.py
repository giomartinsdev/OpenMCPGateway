"""Projections: fold the event log into query-optimised read models.

The write model (aggregates) answers "may I?" but is too chatty for a dashboard
and cannot aggregate across gateways. Projections exist so the API and the
proxy hot path never replay history.

Two rules keep this consistent:
  * a projection is derived state — it can always be rebuilt from `events`
  * updates are idempotent upserts keyed on (aggregate_id) so replaying an
    existing checkpoint is harmless
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from gateway.domain.events import (
    ApiKeyIssued,
    ApiKeyRevoked,
    AuthMode,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayRateLimitSet,
    GatewayUpdated,
    IdentityProvider,
    PrincipalAllowed,
    PrincipalRevoked,
    RequestObserved,
)

def _enum_value(value: Any) -> str | None:
    """Enum member or plain string -> the stored string, None-safe."""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS rm_gateways (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL,
    name          TEXT NOT NULL,
    target_url    TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    auth_mode     TEXT NOT NULL,
    idp           TEXT,
    client_id     TEXT,
    config_url    TEXT,
    required_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    requests_per_minute INTEGER NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    deleted       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uniqueness must be partial: a deleted gateway keeps its slug for the audit
-- trail, but must not block a new gateway from reusing the name.
ALTER TABLE rm_gateways DROP CONSTRAINT IF EXISTS rm_gateways_slug_key;
CREATE UNIQUE INDEX IF NOT EXISTS rm_gateways_live_slug_idx
    ON rm_gateways (slug) WHERE deleted = FALSE;

CREATE TABLE IF NOT EXISTS rm_principals (
    gateway_id TEXT NOT NULL,
    subject    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (gateway_id, subject)
);

CREATE TABLE IF NOT EXISTS rm_api_keys (
    gateway_id TEXT NOT NULL,
    key_id     TEXT NOT NULL,
    label      TEXT NOT NULL,
    key_hash   TEXT NOT NULL,
    subject    TEXT NOT NULL,
    issued_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (gateway_id, key_id)
);

CREATE INDEX IF NOT EXISTS rm_api_keys_hash_idx ON rm_api_keys (key_hash);

CREATE TABLE IF NOT EXISTS rm_request_stats (
    gateway_id  TEXT NOT NULL,
    bucket      TIMESTAMPTZ NOT NULL,   -- minute granularity
    outcome     TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT '',
    count       BIGINT NOT NULL DEFAULT 0,
    total_ms    BIGINT NOT NULL DEFAULT 0,
    max_ms      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (gateway_id, bucket, outcome, ip)
);

CREATE INDEX IF NOT EXISTS rm_stats_bucket_idx ON rm_request_stats (bucket DESC);

CREATE TABLE IF NOT EXISTS rm_requests_tail (
    event_id    TEXT PRIMARY KEY,
    gateway_id  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    status      INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    ip          TEXT,
    tool        TEXT,
    method      TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS rm_tail_time_idx ON rm_requests_tail (occurred_at DESC);
"""


@dataclass(slots=True)
class ProjectionRunner:
    """Applies events to read models, tracking its own checkpoint."""

    pool: asyncpg.Pool
    name: str = "read_models"
    _handlers: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._handlers = {
            GatewayCreated.EVENT: self._on_gateway_created,
            GatewayUpdated.EVENT: self._on_gateway_updated,
            GatewayAuthConfigured.EVENT: self._on_auth_configured,
            GatewayRateLimitSet.EVENT: self._on_rate_limit,
            GatewayDisabled.EVENT: self._on_disabled,
            GatewayEnabled.EVENT: self._on_enabled,
            GatewayDeleted.EVENT: self._on_deleted,
            PrincipalAllowed.EVENT: self._on_principal_allowed,
            PrincipalRevoked.EVENT: self._on_principal_revoked,
            ApiKeyIssued.EVENT: self._on_key_issued,
            ApiKeyRevoked.EVENT: self._on_key_revoked,
            RequestObserved.EVENT: self._on_request,
        }

    async def migrate(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def apply(self, global_seq: int, event: Event) -> None:
        handler = self._handlers.get(event.EVENT)
        if handler is not None:
            await handler(global_seq, event)

    async def _on_gateway_created(self, _seq: int, event: Event) -> None:
        assert isinstance(event, GatewayCreated)
        await self.pool.execute(
            """
            INSERT INTO rm_gateways (id, slug, name, target_url, description,
                                     auth_mode, idp, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8)
            ON CONFLICT (id) DO NOTHING
            """,
            event.aggregate_id,
            event.slug,
            event.name,
            event.target_url,
            event.description,
            _enum_value(event.auth_mode),
            _enum_value(event.idp),
            event.occurred_at,
        )

    async def _on_gateway_updated(self, _seq: int, event: Event) -> None:
        assert isinstance(event, GatewayUpdated)
        await self.pool.execute(
            """
            UPDATE rm_gateways
               SET name        = COALESCE($2, name),
                   target_url  = COALESCE($3, target_url),
                   description = COALESCE($4, description),
                   updated_at  = now()
             WHERE id = $1
            """,
            event.aggregate_id,
            event.name,
            event.target_url,
            event.description,
        )

    async def _on_auth_configured(self, _seq: int, event: Event) -> None:
        assert isinstance(event, GatewayAuthConfigured)
        # client_secret deliberately never copied into the read model.
        await self.pool.execute(
            """
            UPDATE rm_gateways
               SET auth_mode = $2, idp = $3, client_id = $4, config_url = $5,
                   required_scopes = $6::jsonb, updated_at = now()
             WHERE id = $1
            """,
            event.aggregate_id,
            _enum_value(event.auth_mode),
            _enum_value(event.idp),
            event.client_id,
            event.config_url,
            json.dumps(list(event.required_scopes)),
        )

    async def _on_rate_limit(self, _seq: int, event: Event) -> None:
        assert isinstance(event, GatewayRateLimitSet)
        await self.pool.execute(
            "UPDATE rm_gateways SET requests_per_minute = $2, updated_at = now() "
            "WHERE id = $1",
            event.aggregate_id,
            event.requests_per_minute,
        )

    async def _on_disabled(self, _seq: int, event: Event) -> None:
        await self.pool.execute(
            "UPDATE rm_gateways SET enabled = FALSE, updated_at = now() WHERE id = $1",
            event.aggregate_id,
        )

    async def _on_enabled(self, _seq: int, event: Event) -> None:
        await self.pool.execute(
            "UPDATE rm_gateways SET enabled = TRUE, updated_at = now() WHERE id = $1",
            event.aggregate_id,
        )

    async def _on_deleted(self, _seq: int, event: Event) -> None:
        # Slug is kept for the audit trail; the partial unique index only covers
        # live rows, so the name is free to reuse.
        await self.pool.execute(
            "UPDATE rm_gateways SET deleted = TRUE, enabled = FALSE, "
            "updated_at = now() WHERE id = $1",
            event.aggregate_id,
        )

    async def _on_principal_allowed(self, _seq: int, event: Event) -> None:
        assert isinstance(event, PrincipalAllowed)
        await self.pool.execute(
            """
            INSERT INTO rm_principals (gateway_id, subject, note, added_at, revoked)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (gateway_id, subject)
            DO UPDATE SET revoked = FALSE, note = EXCLUDED.note
            """,
            event.aggregate_id,
            event.subject,
            event.note,
            event.occurred_at,
        )

    async def _on_principal_revoked(self, _seq: int, event: Event) -> None:
        assert isinstance(event, PrincipalRevoked)
        await self.pool.execute(
            "UPDATE rm_principals SET revoked = TRUE "
            "WHERE gateway_id = $1 AND subject = $2",
            event.aggregate_id,
            event.subject,
        )

    async def _on_key_issued(self, _seq: int, event: Event) -> None:
        assert isinstance(event, ApiKeyIssued)
        await self.pool.execute(
            """
            INSERT INTO rm_api_keys (gateway_id, key_id, label, key_hash, subject,
                                     issued_at, revoked)
            VALUES ($1,$2,$3,$4,$5,$6,FALSE)
            ON CONFLICT (gateway_id, key_id) DO UPDATE SET revoked = FALSE
            """,
            event.aggregate_id,
            event.key_id,
            event.label,
            event.key_hash,
            event.subject,
            event.occurred_at,
        )

    async def _on_key_revoked(self, _seq: int, event: Event) -> None:
        assert isinstance(event, ApiKeyRevoked)
        await self.pool.execute(
            "UPDATE rm_api_keys SET revoked = TRUE "
            "WHERE gateway_id = $1 AND key_id = $2",
            event.aggregate_id,
            event.key_id,
        )

    async def _on_request(self, _seq: int, event: Event) -> None:
        assert isinstance(event, RequestObserved)
        bucket = event.occurred_at.replace(second=0, microsecond=0)
        await self.pool.execute(
            """
            INSERT INTO rm_requests_tail (event_id, gateway_id, subject, outcome,
                                          status, duration_ms, ip, tool, method,
                                          occurred_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.event_id,
            event.gateway_id,
            event.subject,
            event.outcome,
            event.status,
            event.duration_ms,
            event.ip,
            event.tool,
            event.method,
            event.occurred_at,
        )
        await self.pool.execute(
            """
            INSERT INTO rm_request_stats (gateway_id, bucket, outcome, ip,
                                          count, total_ms, max_ms)
            VALUES ($1,$2,$3,$4,1,$5::bigint,$5::integer)
            ON CONFLICT (gateway_id, bucket, outcome, ip)
            DO UPDATE SET count    = rm_request_stats.count + 1,
                          total_ms = rm_request_stats.total_ms + EXCLUDED.total_ms,
                          max_ms   = GREATEST(rm_request_stats.max_ms, EXCLUDED.max_ms)
            """,
            event.gateway_id,
            bucket,
            event.outcome,
            event.ip or "",
            event.duration_ms,
        )


# --------------------------------------------------------------------------
# Queries against read models
# --------------------------------------------------------------------------


def _gateway_row(row: Any) -> dict[str, Any]:
    """Normalise one `rm_gateways` row.

    asyncpg has no codec for `jsonb` here, so it hands back the raw text. Left
    alone, `["read:user"]` becomes a string and iterating it yields `[`, `"`, ... —
    which is exactly how `scopes_supported` once advertised `["[", "]"]`.
    """
    gateway = dict(row)
    scopes = gateway.get("required_scopes")
    if isinstance(scopes, str):
        gateway["required_scopes"] = json.loads(scopes or "[]")
    return gateway


@dataclass(slots=True)
class ReadModels:
    pool: asyncpg.Pool

    async def list_gateways(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT * FROM rm_gateways WHERE deleted = FALSE ORDER BY created_at"
        )
        return [_gateway_row(row) for row in rows]

    async def get_gateway(self, gateway_id: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM rm_gateways WHERE id = $1", gateway_id
        )
        return _gateway_row(row) if row else None

    async def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM rm_gateways WHERE slug = $1 AND deleted = FALSE", slug
        )
        return _gateway_row(row) if row else None

    async def principals(self, gateway_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT subject, note, added_at FROM rm_principals "
            "WHERE gateway_id = $1 AND revoked = FALSE ORDER BY added_at",
            gateway_id,
        )
        return [dict(row) for row in rows]

    async def api_keys(self, gateway_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT key_id, label, subject, issued_at FROM rm_api_keys "
            "WHERE gateway_id = $1 AND revoked = FALSE ORDER BY issued_at",
            gateway_id,
        )
        return [dict(row) for row in rows]

    async def subject_for_key_hash(self, gateway_id: str,
                                   key_hash: str) -> str | None:
        return await self.pool.fetchval(
            "SELECT subject FROM rm_api_keys "
            "WHERE gateway_id = $1 AND key_hash = $2 AND revoked = FALSE",
            gateway_id,
            key_hash,
        )

    async def request_tail(self, gateway_id: str | None, limit: int = 100) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT gateway_id, subject, outcome, status, duration_ms, ip, tool,
                   method, occurred_at
              FROM rm_requests_tail
             WHERE ($1::text IS NULL OR gateway_id = $1)
             ORDER BY occurred_at DESC
             LIMIT $2
            """,
            gateway_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def stats(self, gateway_id: str | None = None,
                    minutes: int = 60) -> dict[str, Any]:
        """Aggregate traffic: totals, per-outcome, per-IP, latency percentiles."""
        rows = await self.pool.fetch(
            """
            SELECT gateway_id, outcome, ip, count, total_ms, max_ms, bucket
              FROM rm_request_stats
             WHERE bucket >= now() - ($1 || ' minutes')::interval
               AND ($2::text IS NULL OR gateway_id = $2)
            """,
            str(minutes),
            gateway_id,
        )
        total = 0
        total_ms = 0
        by_outcome: dict[str, int] = defaultdict(int)
        by_ip: dict[str, int] = defaultdict(int)
        by_gateway: dict[str, int] = defaultdict(int)
        series: dict[str, int] = defaultdict(int)
        peak_ms = 0
        for row in rows:
            count = int(row["count"])
            total += count
            total_ms += int(row["total_ms"])
            peak_ms = max(peak_ms, int(row["max_ms"]))
            by_outcome[row["outcome"]] += count
            by_ip[row["ip"] or "unknown"] += count
            by_gateway[row["gateway_id"]] += count
            series[row["bucket"].isoformat()] = (
                series[row["bucket"].isoformat()] + count
            )
        return {
            "window_minutes": minutes,
            "requests": total,
            "avg_duration_ms": round(total_ms / total, 1) if total else 0,
            "max_duration_ms": peak_ms,
            "by_outcome": dict(by_outcome),
            "by_ip": dict(sorted(by_ip.items(), key=lambda kv: -kv[1])[:20]),
            "by_gateway": dict(by_gateway),
            "series": [{"bucket": k, "count": v} for k, v in sorted(series.items())],
        }


def utcnow() -> datetime:
    return datetime.now(UTC)
