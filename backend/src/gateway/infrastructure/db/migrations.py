"""Schema history: numbered steps, applied once, recorded in the database.

`create_all` answers "is something missing", which is not the same question as
"which change, in which order, has this installation already seen". A pod that
boots against a database from a previous release needs the second answer, and so
does anyone reading the diff. Hence a list: version 1 is the baseline (`create_all`
itself, so a fresh database and a ten-year-old one converge), and every later
version is one readable step.

One transaction covers the whole run. Postgres DDL is transactional, so a failing
step rolls the schema back to the last good version instead of leaving half of it
applied; the advisory lock makes two pods booting at once queue rather than
interleave.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from gateway.domain.events import TENANT_OF_DEPLOYMENT
from gateway.infrastructure.db.tables import metadata
from gateway.log import log

Step = Callable[[AsyncConnection], Awaitable[None]]

# Any stable number: it only has to be the same one on every pod.
_LOCK = 8_142_337

_HISTORY = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Step


async def _baseline(connection: AsyncConnection) -> None:
    """Every table as it first shipped.

    Still `create_all` rather than 12 hand-written `CREATE TABLE`s: re-typing the
    schema a second time is one more place for the two to disagree, and the
    declaration in `tables.py` is already the truth.
    """
    await connection.run_sync(metadata.create_all)


async def _tenancy(connection: AsyncConnection) -> None:
    """`tenant_id` arrived after these tables did, so old rows need a default.

    `IF NOT EXISTS` on purpose: the column also lands in the baseline for anyone
    starting now, and a migration that only works on one of the two paths is a
    migration that breaks the other.
    """
    for statement in (
        "ALTER TABLE rm_operators ADD COLUMN IF NOT EXISTS tenant_id TEXT",
        f"ALTER TABLE rm_gateways ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL "
        f"DEFAULT '{TENANT_OF_DEPLOYMENT}'",
        f"ALTER TABLE rm_roles ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL "
        f"DEFAULT '{TENANT_OF_DEPLOYMENT}'",
    ):
        await connection.execute(text(statement))


async def _key_reveal_and_tools(connection: AsyncConnection) -> None:
    """The tool allowlist, and the sealed copy of an API key.

    `IF NOT EXISTS` for the same reason as `_tenancy`: the columns are also in
    the baseline, and one of the two paths always starts from nothing.
    """
    for statement in (
        "ALTER TABLE rm_gateways ADD COLUMN IF NOT EXISTS tools JSONB "
        "NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE rm_api_keys ADD COLUMN IF NOT EXISTS sealed TEXT "
        "NOT NULL DEFAULT ''",
        "ALTER TABLE rm_api_keys ADD COLUMN IF NOT EXISTS revealed_at TIMESTAMPTZ",
        "ALTER TABLE rm_api_keys ADD COLUMN IF NOT EXISTS revealed_by TEXT",
    ):
        await connection.execute(text(statement))


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", _baseline),
    Migration(2, "tenancy", _tenancy),
    Migration(3, "key_reveal_and_tools", _key_reveal_and_tools),
)


async def run(engine: AsyncEngine) -> list[Migration]:
    """Apply whatever is missing. Returns the steps this call performed."""
    applied: list[Migration] = []
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
        await connection.execute(text(_HISTORY))
        seen = set(
            (await connection.execute(text("SELECT version FROM schema_migrations"))).scalars()
        )
        for migration in MIGRATIONS:
            if migration.version in seen:
                continue
            await migration.up(connection)
            await connection.execute(
                text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                {"v": migration.version, "n": migration.name},
            )
            applied.append(migration)
            log.info(
                "database.migration", version=migration.version, name=migration.name
            )
    log.info("database.migrated", tables=len(metadata.tables), applied=len(applied))
    return applied


__all__ = ["MIGRATIONS", "Migration", "run"]
