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
  upstream OAuth secrets; only `load_stream` (write model) reads them.

This is the adapter for the `gateway.application.ports.EventStore` port. Commands
depend on the protocol, so a different log (in-memory for a domain test, another
database) is a wiring change rather than an import rewrite.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from gateway.domain.events import ALL_EVENTS, Event
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.db.tables import (
    events,
    gateway_slugs,
    operator_emails,
    projection_checkpoints,
)

# Registry: event_type -> class. Built from the domain modules so a new event
# cannot be appended without also being readable back.
_REGISTRY: dict[str, type[Event]] = {cls.EVENT: cls for cls in ALL_EVENTS}


class ConcurrencyError(RuntimeError):
    """Another writer appended to this stream since we folded it."""


class PostgresEventStore:
    """Append-only event log on Postgres, shared by all app instances."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- write side -------------------------------------------------------

    async def load_stream(
        self, aggregate_id: str, up_to: int | None = None
    ) -> list[Event]:
        query = select(events).where(events.c.aggregate_id == aggregate_id)
        if up_to is not None:
            query = query.where(events.c.sequence <= up_to)
        async with self._db.session() as session:
            rows = (await session.execute(query.order_by(events.c.sequence))).mappings()
        return [_to_event(row) for row in rows]

    async def append(
        self,
        events_: Sequence[Event],
        *,
        expected_version: int,
        actor: str = "system",
    ) -> list[Event]:
        """Append events, enforcing that the stream is still at `expected_version`."""
        return await self._append(
            events_, expected_version=expected_version, actor=actor, versioned=True
        )

    async def append_unversioned(
        self, events_: Sequence[Event], *, actor: str = "system"
    ) -> list[Event]:
        """Append to a stream nobody reads back (traffic observations).

        Still sequential and append-only, but skips the read-then-check race so
        concurrent writers never fail each other. Only use for streams that are
        never folded into a write model.
        """
        return await self._append(
            events_, expected_version=None, actor=actor, versioned=False
        )

    async def _append(
        self,
        batch: Sequence[Event],
        *,
        expected_version: int | None,
        actor: str,
        versioned: bool,
    ) -> list[Event]:
        if not batch:
            return []

        aggregate_id = batch[0].aggregate_id
        async with self._db.session() as session, session.begin():
            if versioned:
                current = await session.scalar(
                    select(func.coalesce(func.max(events.c.sequence), 0)).where(
                        events.c.aggregate_id == aggregate_id
                    )
                )
                if current != expected_version:
                    raise ConcurrencyError(
                        f"stream {aggregate_id} at {current}, "
                        f"expected {expected_version}"
                    )
                base = expected_version
            else:
                # A row lock cannot be taken on an aggregate (Postgres forbids
                # FOR UPDATE with MAX), so serialize this stream with a
                # transaction-scoped advisory lock instead.
                await session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtext(aggregate_id)))
                )
                base = await session.scalar(
                    select(func.coalesce(func.max(events.c.sequence), 0)).where(
                        events.c.aggregate_id == aggregate_id
                    )
                )

            prepared = [
                event.model_copy(
                    update={"sequence": base + offset, "actor": actor}
                )
                for offset, event in enumerate(batch, start=1)
            ]
            try:
                await session.execute(
                    pg_insert(events),
                    [
                        {
                            "event_id": event.event_id,
                            "aggregate_id": event.aggregate_id,
                            "sequence": event.sequence,
                            "event_type": event.EVENT,
                            "occurred_at": event.occurred_at,
                            "actor": event.actor,
                            "payload": event.payload(),
                        }
                        for event in prepared
                    ],
                )
            except IntegrityError as exc:
                raise ConcurrencyError(str(exc.orig)) from exc
        return prepared

    # -- read side --------------------------------------------------------

    async def read_all(
        self, *, since: int = 0, until: int | None = None, batch_size: int = 500
    ) -> AsyncIterator[tuple[int, Event]]:
        """Yield (global_seq, event) in global order — used by projections."""
        cursor = since
        while True:
            query = select(events).where(events.c.global_seq > cursor)
            if until is not None:
                query = query.where(events.c.global_seq <= until)
            async with self._db.session() as session:
                rows = (
                    await session.execute(
                        query.order_by(events.c.global_seq).limit(batch_size)
                    )
                ).mappings()
                batch = list(rows)
            if not batch:
                return
            for row in batch:
                cursor = row["global_seq"]
                yield cursor, _to_event(row)

    async def events_for(
        self, aggregate_id: str, *, limit: int = 50
    ) -> list[tuple[int, Event]]:
        query = (
            select(events)
            .where(events.c.aggregate_id == aggregate_id)
            .order_by(events.c.sequence.desc())
            .limit(limit)
        )
        async with self._db.session() as session:
            rows = (await session.execute(query)).mappings()
            return [(row["global_seq"], _to_event(row)) for row in rows]

    async def recent_events(
        self, *, limit: int = 100, aggregate_ids: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Newest events first, redacted.

        `aggregate_ids` is how the audit page stays inside one tenant: the log is
        shared, so the caller names the streams it owns. The store does not know
        what a tenant is — it only knows how to read by aggregate.
        """
        query = select(events)
        if aggregate_ids is not None:
            query = query.where(events.c.aggregate_id.in_(list(aggregate_ids)))
        query = query.order_by(events.c.global_seq.desc()).limit(limit)
        async with self._db.session() as session:
            rows = (await session.execute(query)).mappings()
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
        async with self._db.session() as session:
            value = await session.scalar(
                select(projection_checkpoints.c.global_seq).where(
                    projection_checkpoints.c.name == name
                )
            )
        return int(value or 0)

    async def save_checkpoint(self, name: str, global_seq: int) -> None:
        statement = pg_insert(projection_checkpoints).values(
            name=name, global_seq=global_seq, updated_at=func.now()
        )
        statement = statement.on_conflict_do_update(
            index_elements=[projection_checkpoints.c.name],
            set_={
                "global_seq": statement.excluded.global_seq,
                "updated_at": func.now(),
            },
        )
        async with self._db.session() as session, session.begin():
            await session.execute(statement)

    async def reset_projections(self) -> None:
        async with self._db.session() as session, session.begin():
            await session.execute(delete(projection_checkpoints))

    # -- slug index -------------------------------------------------------

    async def claim_slug(self, slug: str, aggregate_id: str) -> None:
        try:
            async with self._db.session() as session, session.begin():
                await session.execute(
                    pg_insert(gateway_slugs).values(
                        slug=slug, aggregate_id=aggregate_id
                    )
                )
        except IntegrityError as exc:
            raise ConcurrencyError(f"slug '{slug}' already taken") from exc

    async def release_slug(self, slug: str) -> None:
        async with self._db.session() as session, session.begin():
            await session.execute(
                delete(gateway_slugs).where(gateway_slugs.c.slug == slug)
            )

    async def resolve_slug(self, slug: str) -> str | None:
        async with self._db.session() as session:
            return await session.scalar(
                select(gateway_slugs.c.aggregate_id).where(
                    gateway_slugs.c.slug == slug
                )
            )

    async def index_operator_email(self, email: str, aggregate_id: str) -> None:
        statement = pg_insert(operator_emails).values(
            email=email.lower(), aggregate_id=aggregate_id
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[operator_emails.c.email]
        )
        async with self._db.session() as session, session.begin():
            await session.execute(statement)

    async def fetch_operator_by_email(self, email: str) -> str | None:
        async with self._db.session() as session:
            return await session.scalar(
                select(operator_emails.c.aggregate_id).where(
                    operator_emails.c.email == email.lower()
                )
            )

    async def fetch_tenant_by_code(self, join_code: str) -> dict[str, str] | None:
        """Tenant id and name for a join code, case-insensitively.

        Codes are shown uppercase and typed by hand, so a lowercase entry must
        work; the read model stores what the event carried.
        """
        query = select(events.c.aggregate_id).where(
            (events.c.event_type == "tenant.created")
            & (func.upper(events.c.payload["join_code"].astext) == join_code.strip().upper())
        )
        async with self._db.session() as session:
            row = (await session.execute(query)).first()
            if row is None:
                return None
            tenant_id = row[0]
            name = await session.scalar(
                select(events.c.payload["name"].astext).where(
                    events.c.aggregate_id == tenant_id
                )
            )
        return {"id": tenant_id, "name": name or ""}

    async def join_code_taken(self, join_code: str) -> bool:
        return await self.fetch_tenant_by_code(join_code) is not None

    async def count_operators(self) -> int:
        async with self._db.session() as session:
            return int(
                await session.scalar(select(func.count()).select_from(operator_emails))
                or 0
            )

    async def list_operators(self) -> list[dict[str, Any]]:
        """Accounts and their tenant role, oldest first.

        Read from the log rather than a table: `operator.registered` is the only
        event that can create an account, so a projection would be a copy of one
        JSONB read with a migration attached.
        """
        query = (
            select(
                events.c.aggregate_id,
                events.c.payload["email"].astext.label("email"),
                events.c.payload["is_admin"].astext.label("is_admin"),
                events.c.occurred_at,
            )
            .where(events.c.event_type == "operator.registered")
            .order_by(events.c.global_seq)
        )
        async with self._db.session() as session:
            rows = (await session.execute(query)).mappings()
            return [
                {
                    "operator_id": row["aggregate_id"],
                    "email": row["email"],
                    "role": "admin" if row["is_admin"] == "true" else "member",
                    "registered_at": row["occurred_at"].isoformat(),
                }
                for row in rows
            ]

    async def all_slugs(self) -> dict[str, str]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(gateway_slugs.c.slug, gateway_slugs.c.aggregate_id)
                )
            ).all()
        return {slug: aggregate_id for slug, aggregate_id in rows}


# -- rehydration ----------------------------------------------------------


def _to_event(row: Any) -> Event:
    """Row -> concrete event. Pydantic restores enums, tuples and datetimes.

    JSON has no enum type, so `auth_mode` comes back as a string. The domain
    compares enum members with `is`, which silently misbehaves on equal-but-
    distinct strings — this is the single place that has to be right.
    """
    event_type = row["event_type"]
    cls = _REGISTRY.get(event_type)
    if cls is None:
        raise KeyError(f"unknown event type '{event_type}' — register it")
    return cls.model_validate(
        {
            "aggregate_id": row["aggregate_id"],
            "sequence": row["sequence"],
            "event_id": row["event_id"],
            "occurred_at": row["occurred_at"],
            "actor": row["actor"],
            **(row["payload"] or {}),
        }
    )


_SECRET_KEYS = {
    "password_hash",
    "key_hash",
    "client_secret",
    "upstream_token",
    "google_client_secret",
}


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


__all__ = ["ConcurrencyError", "PostgresEventStore"]
