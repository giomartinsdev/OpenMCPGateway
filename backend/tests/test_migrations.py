"""The schema history is a list, so the tests are about the list, not the DDL.

The rest of the suite already proves a fresh database ends up usable: every
scenario builds one through `Container.create`, which runs the migrations. What
is left to prove is the part a fresh database cannot show — that the history is
recorded, and that a second boot applies nothing.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gateway.infrastructure.db.database import as_async_dsn
from gateway.infrastructure.db.migrations import MIGRATIONS, run


def test_the_history_is_recorded_once(runner, postgres) -> None:
    engine = create_async_engine(as_async_dsn(postgres))

    async def scenario() -> tuple[list[int], list]:
        await run(engine)
        again = await run(engine)
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("SELECT version FROM schema_migrations ORDER BY version")
                )
            ).scalars().all()
        await engine.dispose()
        return list(rows), again

    versions, applied = runner.run(scenario())
    assert versions == [migration.version for migration in MIGRATIONS], versions
    # A database that is already current does no work — that is what makes it
    # safe to run on every boot instead of in a job of its own.
    assert applied == [], applied


def test_every_read_model_lives_in_its_own_schema(runner, postgres) -> None:
    """`readmodels` holds the projections; nothing the log needs is in there.

    This is the invariant behind "drop the schema and reboot": if `events` or a
    uniqueness table ever moved in, that reset would destroy the log and the
    promise would be a lie.
    """
    engine = create_async_engine(as_async_dsn(postgres))

    async def scenario() -> dict[str, list[str]]:
        await run(engine)
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT schemaname, tablename FROM pg_tables "
                        "WHERE schemaname IN ('public', 'readmodels') "
                        "ORDER BY schemaname, tablename"
                    )
                )
            ).all()
        await engine.dispose()
        placed: dict[str, list[str]] = {}
        for schema, table in rows:
            placed.setdefault(schema, []).append(table)
        return placed

    placed = runner.run(scenario())
    assert placed["readmodels"] == [
        "rm_api_keys",
        "rm_gateways",
        "rm_operators",
        "rm_principals",
        "rm_request_stats",
        "rm_requests_tail",
        "rm_roles",
        "rm_tenants",
        "rm_tool_fingerprints",
        "rm_webhook_deliveries",
        "rm_webhooks",
    ], placed
    # The log and the uniqueness tables stay behind, and so does anything a
    # library creates for itself (`oauth_state`, from the OAuth proxy's store).
    assert "events" in placed["public"], placed
    assert not [name for name in placed["public"] if name.startswith("rm_")], placed


def test_dropping_the_read_model_schema_is_recoverable(runner, postgres) -> None:
    """The reset an operator is told to perform has to actually work."""
    engine = create_async_engine(as_async_dsn(postgres))

    async def scenario() -> tuple[object, object]:
        await run(engine)
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA readmodels CASCADE"))
        await run(engine)
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT to_regclass('readmodels.rm_gateways')")
            )
            events = await connection.scalar(text("SELECT to_regclass('public.events')"))
        await engine.dispose()
        return exists, events

    exists, events = runner.run(scenario())
    assert exists == "readmodels.rm_gateways", exists
    # The log is the thing a projection is rebuilt *from*; losing it would make
    # the rebuild impossible, so it must survive the drop.
    assert events is not None, "the drop took the event log with it"


def test_an_existing_installation_is_converted_not_rebuilt(runner, postgres) -> None:
    """The upgrade path: tables in `public`, data in them, history at version 3.

    A fresh database proves the destination, not the journey. This rebuilds the
    pre-0.20 layout by hand — `rm_*` in `public`, its rows, and only migrations 1-3
    recorded — then runs the real migration list over it and checks that the rows
    arrived in `readmodels` and were not recreated empty.
    """
    engine = create_async_engine(as_async_dsn(postgres))
    old = MIGRATIONS[:3]

    async def scenario() -> tuple[object, object, list[str]]:
        async with engine.begin() as connection:
            # Start from the pre-0.20 shape, whatever the previous test left.
            await connection.execute(text("DROP SCHEMA IF EXISTS readmodels CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS schema_migrations"))
            await connection.execute(
                text("DROP TABLE IF EXISTS public.rm_gateways")
            )
            await connection.execute(text("CREATE TABLE schema_migrations ("
                                          "version INTEGER PRIMARY KEY, "
                                          "name TEXT NOT NULL, "
                                          "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
            await connection.execute(
                text("CREATE TABLE public.rm_gateways ("
                     "id TEXT PRIMARY KEY, slug TEXT NOT NULL, name TEXT NOT NULL, "
                     "target_url TEXT NOT NULL, auth_mode TEXT NOT NULL)")
            )
            await connection.execute(
                text("INSERT INTO public.rm_gateways VALUES "
                     "('gw_old', 'legacy', 'Legacy', 'http://x/mcp', 'none')")
            )
            for migration in old:
                await connection.execute(
                    text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                    {"v": migration.version, "n": migration.name},
                )
        await run(engine)
        async with engine.connect() as connection:
            moved = await connection.scalar(
                text("SELECT to_regclass('readmodels.rm_gateways')")
            )
            stale = await connection.scalar(
                text("SELECT to_regclass('public.rm_gateways')")
            )
            slug = await connection.scalar(
                text("SELECT slug FROM readmodels.rm_gateways WHERE id = 'gw_old'")
            )
        await engine.dispose()
        return moved, stale, [slug]

    moved, stale, slugs = runner.run(scenario())
    assert moved == "readmodels.rm_gateways", moved
    assert stale is None, "the table was copied, not moved"
    # The row survived: a conversion that recreates an empty table would look
    # identical in the catalog and destroy the panel.
    assert slugs == ["legacy"], slugs
