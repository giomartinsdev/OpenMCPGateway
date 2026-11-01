"""Postgres event store — the single durable record of what happened.

Design notes that matter for correctness:

* **Append-only.** No UPDATE, no DELETE on `events`. A correction is a new event.
* **Optimistic concurrency.** `append` takes the sequence the caller folded
  from; the unique index on `(aggregate_id, sequence)` turns a concurrent write
  into an IntegrityError, which we surface as `ConcurrencyError` instead of
  silently interleaving two writers' histories.
* **Global order.** `global_seq` is a BIGSERIAL giving every event a total order
  across aggregates — that is what makes read models (projections) replayable
  from a checkpoint and what the stats queries rely on.
* **No secrets in the read side.** Payloads contain password/key hashes and
  upstream OAuth secrets; only `_load_stream` (write model) reads them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

import asyncpg

from gateway.domain.events import Event

# Registry: event_type -> class. Populated by `register_events` so the store can
# rebuild concrete event objects from rows without importing every module.
_REGISTRY: dict[str, type[Event]] = {}


def register_events(*classes: type[Event]) -> None:
    for cls in classes:
        _REGISTRY[cls.EVENT] = cls


class ConcurrencyError(RuntimeError):
    """Another writer appended to this stream since we folded it."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    global_seq   BIGSERIAL   PRIMARY KEY,
    event_id     TEXT        NOT NULL UNIQUE,
    aggregate_id TEXT        NOT NULL,
    sequence     INTEGER     NOT NULL,
    event_type   TEXT        NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,
    actor        TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    UNIQUE (aggregate_id, sequence)
);

CREATE INDEX IF NOT EXISTS events_aggregate_idx ON events (aggregate_id, sequence);
CREATE INDEX IF NOT EXISTS events_type_idx ON events (event_type, global_seq);
CREATE INDEX IF NOT EXISTS events_occurred_idx ON events (occurred_at DESC);

-- Projection checkpoints: one row per read model, so a rebuild resumes where it
-- stopped instead of replaying the whole log.
CREATE TABLE IF NOT EXISTS projection_checkpoints (
    name        TEXT    PRIMARY KEY,
    global_seq  BIGINT  NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lookup index for slugs. Slugs are unique across gateways, and this is the hot
-- path for every proxied request, so it is a real table rather than a scan.
CREATE TABLE IF NOT EXISTS gateway_slugs (
    slug        TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL UNIQUE
);

-- Login is a hot path and email lives inside an event payload, so keep an
-- explicit unique index instead of scanning every operator stream.
CREATE TABLE IF NOT EXISTS operator_emails (
    email        TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL UNIQUE
);
"""


class PostgresEventStore:
    """Append-only event log on Postgres, shared by all app instances."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 2,
                      max_size: int = 10) -> "PostgresEventStore":
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        store = cls(pool)
        await store.migrate()
        return store

    async def migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def close(self) -> None:
        # `terminate()` rather than `close()`: close() waits for every connection
        # to be released, and on shutdown a connection can be parked mid-statement
        # by a cancelled request — the wait never ends and the pod is SIGKILLed,
        # losing the graceful path entirely. Nothing here needs a clean session
        # teardown; the events are already committed.
        # terminate() is synchronous by design (nothing is gracefully released).
        self._pool.terminate()

    # -- write side -------------------------------------------------------

    async def load_stream(
        self, aggregate_id: str, up_to: int | None = None
    ) -> list[Event]:
        rows = await self._pool.fetch(
            """
            SELECT event_id, aggregate_id, sequence, event_type, occurred_at,
                   actor, payload
              FROM events
             WHERE aggregate_id = $1
               AND ($2::int IS NULL OR sequence <= $2)
             ORDER BY sequence
            """,
            aggregate_id,
            up_to,
        )
        return [self._to_event(row) for row in rows]

    async def append(
        self,
        events: Sequence[Event],
        *,
        expected_version: int,
        actor: str = "system",
    ) -> list[Event]:
        """Append events, enforcing that the stream is still at `expected_version`."""
        return await self._append(
            events, expected_version=expected_version, actor=actor, versioned=True
        )

    async def append_unversioned(
        self, events: Sequence[Event], *, actor: str = "system"
    ) -> list[Event]:
        """Append to a stream nobody reads back (traffic observations).

        Still sequential and append-only, but skips the read-then-check race so
        concurrent writers never fail each other. Only use for streams that are
        never folded into a write model.
        """
        return await self._append(
            events, expected_version=None, actor=actor, versioned=False
        )

    async def _append(
        self,
        events: Sequence[Event],
        *,
        expected_version: int | None,
        actor: str,
        versioned: bool,
    ) -> list[Event]:
        if not events:
            return []

        aggregate_id = events[0].aggregate_id
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if versioned:
                    current = await conn.fetchval(
                        "SELECT COALESCE(MAX(sequence), 0) FROM events "
                        "WHERE aggregate_id = $1",
                        aggregate_id,
                    )
                    if current != expected_version:
                        raise ConcurrencyError(
                            f"stream {aggregate_id} at {current}, "
                            f"expected {expected_version}"
                        )
                    base = expected_version
                else:
                    # A row lock cannot be taken on an aggregate (Postgres
                    # forbids FOR UPDATE with MAX), so serialize this stream with
                    # a transaction-scoped advisory lock instead.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))", aggregate_id
                    )
                    base = await conn.fetchval(
                        "SELECT COALESCE(MAX(sequence), 0) FROM events "
                        "WHERE aggregate_id = $1",
                        aggregate_id,
                    )

                prepared = [
                    replace(
                        event,
                        sequence=base + offset,
                        actor=actor,
                    )
                    for offset, event in enumerate(events, start=1)
                ]
                try:
                    await conn.executemany(
                        """
                        INSERT INTO events (event_id, aggregate_id, sequence,
                                            event_type, occurred_at, actor, payload)
                        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                        """,
                        [
                            (
                                e.event_id,
                                e.aggregate_id,
                                e.sequence,
                                e.EVENT,
                                e.occurred_at,
                                e.actor,
                                json.dumps(e.payload()),
                            )
                            for e in prepared
                        ],
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise ConcurrencyError(str(exc)) from exc
        return prepared

        return prepared

    # -- read side --------------------------------------------------------

    async def read_all(
        self, *, since: int = 0, until: int | None = None, batch_size: int = 500
    ) -> AsyncIterator[tuple[int, Event]]:
        """Yield (global_seq, event) in global order — used by projections."""
        cursor = since
        while True:
            rows = await self._pool.fetch(
                """
                SELECT global_seq, event_id, aggregate_id, sequence, event_type,
                       occurred_at, actor, payload
                  FROM events
                 WHERE global_seq > $1
                   AND ($2::bigint IS NULL OR global_seq <= $2)
                 ORDER BY global_seq
                 LIMIT $3
                """,
                cursor,
                until,
                batch_size,
            )
            if not rows:
                return
            for row in rows:
                cursor = row["global_seq"]
                yield cursor, self._to_event(row)

    async def events_for(
        self, aggregate_id: str, *, limit: int = 50
    ) -> list[tuple[int, Event]]:
        rows = await self._pool.fetch(
            """
            SELECT global_seq, event_id, aggregate_id, sequence, event_type,
                   occurred_at, actor, payload
              FROM events
             WHERE aggregate_id = $1
             ORDER BY sequence DESC
             LIMIT $2
            """,
            aggregate_id,
            limit,
        )
        return [(row["global_seq"], self._to_event(row)) for row in rows]

    async def recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT global_seq, event_id, aggregate_id, sequence, event_type,
                   occurred_at, actor, payload
              FROM events
             ORDER BY global_seq DESC
             LIMIT $1
            """,
            limit,
        )
        return [
            {
                "global_seq": row["global_seq"],
                "event_id": row["event_id"],
                "aggregate_id": row["aggregate_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"].isoformat(),
                "actor": row["actor"],
                # Payloads can hold hashes/secrets — redact before serving.
                "payload": _redact(row["payload"]),
            }
            for row in rows
        ]

    # -- projection bookkeeping -------------------------------------------

    async def checkpoint(self, name: str) -> int:
        value = await self._pool.fetchval(
            "SELECT global_seq FROM projection_checkpoints WHERE name = $1", name
        )
        return int(value or 0)

    async def save_checkpoint(self, name: str, global_seq: int) -> None:
        await self._pool.execute(
            """
            INSERT INTO projection_checkpoints (name, global_seq, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (name) DO UPDATE
               SET global_seq = EXCLUDED.global_seq, updated_at = now()
            """,
            name,
            global_seq,
        )

    async def reset_projections(self) -> None:
        await self._pool.execute("DELETE FROM projection_checkpoints")

    # -- slug index -------------------------------------------------------

    async def claim_slug(self, slug: str, aggregate_id: str) -> None:
        try:
            await self._pool.execute(
                "INSERT INTO gateway_slugs (slug, aggregate_id) VALUES ($1, $2)",
                slug,
                aggregate_id,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConcurrencyError(f"slug '{slug}' already taken") from exc

    async def release_slug(self, slug: str) -> None:
        await self._pool.execute(
            "DELETE FROM gateway_slugs WHERE slug = $1", slug
        )

    async def resolve_slug(self, slug: str) -> str | None:
        return await self._pool.fetchval(
            "SELECT aggregate_id FROM gateway_slugs WHERE slug = $1", slug
        )

    async def index_operator_email(self, email: str, aggregate_id: str) -> None:
        await self._pool.execute(
            "INSERT INTO operator_emails (email, aggregate_id) VALUES ($1, $2) "
            "ON CONFLICT (email) DO NOTHING",
            email.lower(),
            aggregate_id,
        )

    async def fetch_operator_by_email(self, email: str) -> str | None:
        return await self._pool.fetchval(
            "SELECT aggregate_id FROM operator_emails WHERE email = $1",
            email.lower(),
        )

    async def all_slugs(self) -> dict[str, str]:
        rows = await self._pool.fetch("SELECT slug, aggregate_id FROM gateway_slugs")
        return {row["slug"]: row["aggregate_id"] for row in rows}

    # -- helpers ----------------------------------------------------------

    def _to_event(self, row: asyncpg.Record) -> Event:
        cls = _REGISTRY.get(row["event_type"])
        if cls is None:
            raise KeyError(f"unknown event type '{row['event_type']}' — register it")
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return cls(
            aggregate_id=row["aggregate_id"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            occurred_at=row["occurred_at"],
            actor=row["actor"],
            **_coerce_enums(payload),
        )


_SECRET_KEYS = {"password_hash", "key_hash", "client_secret", "upstream_token"}


def _coerce_enums(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn stored strings back into enum members.

    JSON has no enum type, so `auth_mode`/`idp` come back as plain strings. The
    domain compares them with `is`, which silently misbehaves on equal-but-
    distinct strings — so rehydration must restore real members.
    """
    from gateway.domain.events import AuthMode, IdentityProvider

    coerced = dict(payload)
    if isinstance(coerced.get("auth_mode"), str):
        coerced["auth_mode"] = AuthMode(coerced["auth_mode"])
    if isinstance(coerced.get("idp"), str):
        coerced["idp"] = IdentityProvider(coerced["idp"])
    if isinstance(coerced.get("required_scopes"), list):
        coerced["required_scopes"] = tuple(coerced["required_scopes"])
    return coerced



def _redact(payload: Any) -> Any:
    """Strip secret material from payloads that leave the process."""
    if isinstance(payload, dict):
        return {
            key: ("<redacted>" if key in _SECRET_KEYS else _redact(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload


def register_all_events() -> None:
    """Register every event class. Called once at startup."""
    from gateway.domain import events as e

    register_events(
        e.OperatorRegistered,
        e.OperatorLoggedIn,
        e.GatewayCreated,
        e.GatewayUpdated,
        e.GatewayAuthConfigured,
        e.GatewayRateLimitSet,
        e.GatewayDisabled,
        e.GatewayEnabled,
        e.GatewayDeleted,
        e.PrincipalAllowed,
        e.PrincipalRevoked,
        e.ApiKeyIssued,
        e.ApiKeyRevoked,
        e.RequestObserved,
    )
