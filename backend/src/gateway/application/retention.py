"""Retention: the traffic log ages out, the audit log never does.

Two logs share two tables and their lifetimes are not the same:

* `events` holds every fact, including `request.observed`, which is one row per
  proxied call. On a busy gateway that is the whole table.
* `rm_requests_tail` and `rm_request_stats` are its projections and carry the
  same growth.

The audit log — who created a gateway, who granted a role — is the record a
tenant is accountable for, so it is never pruned. Only `request.observed` rows
and their projections are. That is a deliberate asymmetry, not an oversight:
"what did we serve last month" is a debugging aid with a horizon, "who changed
this" is not.

The cutoff is applied in one transaction across the three tables, so a request
row can never outlive the event it was projected from. `0` means keep everything,
which is the default: an installation that has never set a horizon must not lose
history because a default was guessed.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, text

from gateway.infrastructure.db.database import Database
from gateway.infrastructure.db.tables import (
    events,
    rm_request_stats,
    rm_requests_tail,
)
from gateway.log import log

#: The event type that retention may delete. Everything else is audit.
TRAFFIC_EVENT = "request.observed"

class Retention:
    """Prunes traffic older than a horizon, and nothing else."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def prune(self, days: int) -> int:
        """Delete traffic older than `days`. Returns how many events went.

        `days <= 0` is the documented "keep everything" and returns 0 without
        touching the database: a zero that means "unlimited" must not be read as
        "delete everything", which is what `now - 0 days` would mean.
        """
        if days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._db.session() as session, session.begin():
            # `rm_requests_tail` first: it is keyed by event id, so deleting the
            # events first would leave rows whose source is gone and a later
            # rebuild would resurrect them.
            tail = await session.execute(
                delete(rm_requests_tail).where(rm_requests_tail.c.occurred_at < cutoff)
            )
            await session.execute(
                delete(rm_request_stats).where(rm_request_stats.c.bucket < cutoff)
            )
            removed = await session.execute(
                delete(events).where(
                    (events.c.event_type == TRAFFIC_EVENT)
                    & (events.c.occurred_at < cutoff)
                )
            )
        deleted = removed.rowcount or 0
        if deleted:
            log.info(
                "retention.pruned",
                days=days,
                events=deleted,
                tail_rows=tail.rowcount or 0,
            )
        return deleted

    async def vacuum(self) -> None:
        """Reclaim the space. Postgres does not return it on `DELETE`.

        `VACUUM` cannot run inside a transaction, hence its own connection and
        `AUTOCOMMIT`. Called after a prune, not on every boot: a full vacuum on
        a healthy deployment is a table scan for nothing.
        """
        engine = self._db.engine
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text("VACUUM (ANALYZE) events"))
            await connection.execute(text("VACUUM (ANALYZE) rm_requests_tail"))

__all__ = ["TRAFFIC_EVENT", "Retention"]
