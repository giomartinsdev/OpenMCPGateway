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
from gateway.infrastructure.db.tables import READMODELS_SCHEMA, metadata
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
    # Explicit, not left to `create_all`'s own schema handling: migration 4 runs
    # against this schema on a fresh database too, and a missing schema there is
    # an error instead of a no-op.
    await connection.execute(
        text(f"CREATE SCHEMA IF NOT EXISTS {READMODELS_SCHEMA}")
    )
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


async def _readmodels_schema(connection: AsyncConnection) -> None:
    """Move every `rm_*` table from `public` into `readmodels`.

    A read model is rebuildable by definition, so it deserves a schema you can
    drop in one statement — `DROP SCHEMA readmodels CASCADE`, then a boot. That
    promise only holds if nothing the event log needs lives there, which is why
    `events`, `schema_migrations` and the uniqueness tables stay in `public`.

    `ALTER TABLE IF EXISTS … SET SCHEMA` is a no-op when the table is already in
    `readmodels`, which is the fresh-database path: `create_all` puts it there
    before this step runs, so the same statement is correct on both paths.
    """
    await connection.execute(
        text(f"CREATE SCHEMA IF NOT EXISTS {READMODELS_SCHEMA}")
    )
    for name in (
        "rm_tenants",
        "rm_roles",
        "rm_operators",
        "rm_gateways",
        "rm_principals",
        "rm_api_keys",
        "rm_request_stats",
        "rm_requests_tail",
    ):
        await connection.execute(
            text(f"ALTER TABLE IF EXISTS public.{name} SET SCHEMA {READMODELS_SCHEMA}")
        )


async def _key_expiry_and_reason(connection: AsyncConnection) -> None:
    """Two columns for two questions the log could not answer.

    `expires_at` turns "this key has been in a CI secret for three years" into a
    401 with a date on it. `reason` on the traffic tail turns "forbidden" into
    which gate refused it. Both are nullable/defaulted so a database that already
    has rows upgrades without a rewrite.
    """
    # Qualified, like migration 4: this step also runs against a database that
    # was just built by the baseline, and the search_path is not guaranteed to
    # include the read-model schema for everyone who calls `run`.
    for statement in (
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_api_keys "
        "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_requests_tail "
        "ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT ''",
    ):
        await connection.execute(text(statement))

async def _correlation_quota_and_hooks(connection: AsyncConnection) -> None:
    """Everything this release adds that is a column, in one readable step.

    Three unrelated features share a version because they share a boot: a
    request id on the traffic tail, a monthly counter beside the per-minute
    limit, and the tables a webhook needs. Splitting them into three versions
    would buy nothing — a pod either has all of them or is on the previous
    release.
    """
    # Qualified, like migrations 4 and 5: this step also runs against a database
    # that the baseline just built, where `search_path` may not include the
    # read-model schema.
    for statement in (
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_requests_tail "
        "ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS monthly_quota INTEGER NOT NULL DEFAULT 0",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS quota_month TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS requests_this_month INTEGER NOT NULL DEFAULT 0",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_operators "
        "ADD COLUMN IF NOT EXISTS disabled BOOLEAN NOT NULL DEFAULT false",
        # `create_all` makes these on a fresh database; on an existing one this
        # is the only thing that does.
        f"CREATE TABLE IF NOT EXISTS {READMODELS_SCHEMA}.rm_tool_fingerprints ("
        "gateway_id TEXT NOT NULL, tool TEXT NOT NULL, fingerprint TEXT NOT NULL, "
        "checked_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (gateway_id, tool))",
        f"CREATE TABLE IF NOT EXISTS {READMODELS_SCHEMA}.rm_webhooks ("
        "id TEXT PRIMARY KEY, gateway_id TEXT NOT NULL, url TEXT NOT NULL, "
        "secret TEXT NOT NULL, outcomes JSONB NOT NULL DEFAULT '[]'::jsonb, "
        "enabled BOOLEAN NOT NULL DEFAULT true, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
        f"CREATE TABLE IF NOT EXISTS {READMODELS_SCHEMA}.rm_webhook_deliveries ("
        "id TEXT PRIMARY KEY, webhook_id TEXT NOT NULL, event TEXT NOT NULL, "
        "status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
        "status_code INTEGER, error TEXT NOT NULL DEFAULT '', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    ):
        await connection.execute(text(statement))

async def _multi_auth(connection: AsyncConnection) -> None:
    """Two columns that let one gateway have more than one way in.

    `allow_api_key` defaults to false and `verifiers` to `[]`, so every gateway
    written before this release keeps exactly the behaviour it had.
    """
    for statement in (
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS allow_api_key BOOLEAN NOT NULL DEFAULT false",
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS verifiers JSONB NOT NULL DEFAULT '[]'::jsonb",
    ):
        await connection.execute(text(statement))

async def _tool_scopes(connection: AsyncConnection) -> None:
    """One column: the per credential source tool allowlist.

    `{}` for every gateway written before this release, which is exactly the
    behaviour they had — one list for everyone.
    """
    await connection.execute(text(
        f"ALTER TABLE IF EXISTS {READMODELS_SCHEMA}.rm_gateways "
        "ADD COLUMN IF NOT EXISTS tool_scopes JSONB NOT NULL DEFAULT '{}'::jsonb"
    ))

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", _baseline),
    Migration(2, "tenancy", _tenancy),
    Migration(3, "key_reveal_and_tools", _key_reveal_and_tools),
    Migration(4, "readmodels_schema", _readmodels_schema),
    Migration(5, "key_expiry_and_reason", _key_expiry_and_reason),
    Migration(6, "correlation_quota_and_hooks", _correlation_quota_and_hooks),
    Migration(7, "multi_auth", _multi_auth),
    Migration(8, "tool_scopes", _tool_scopes),
)


async def run(engine: AsyncEngine) -> list[Migration]:
    """Apply whatever is missing. Returns the steps this call performed."""
    applied: list[Migration] = []
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
        # Older steps name `rm_*` tables without a schema, which was correct when
        # they lived in `public`. `readmodels` on the search path keeps those
        # steps honest on a converted database *and* on a fresh one, where
        # `create_all` already put them there. Transaction-local, so no other
        # connection inherits it.
        await connection.execute(
            text(f"SET LOCAL search_path TO public, {READMODELS_SCHEMA}")
        )
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
            # Not `name=`: `logging` refuses to overwrite a reserved record
            # attribute and `_emit` suppresses the error, so the line vanished.
            log.info(
                "database.migration", version=migration.version, step=migration.name
            )
        # `DROP SCHEMA readmodels CASCADE` is the documented way to rebuild every
        # projection. History still says version 1 ran, so nothing would recreate
        # them — the log would survive and the read models would not. One catalog
        # lookup per boot buys that promise; on the normal path nothing is created.
        missing = await connection.scalar(
            text(f"SELECT to_regclass('{READMODELS_SCHEMA}.rm_gateways')")
        )
        if missing is None:
            await connection.execute(
                text(f"CREATE SCHEMA IF NOT EXISTS {READMODELS_SCHEMA}")
            )
            await connection.run_sync(metadata.create_all)
            log.info("database.readmodels_recreated")
    log.info("database.migrated", tables=len(metadata.tables), applied=len(applied))
    return applied


__all__ = ["MIGRATIONS", "Migration", "run"]
