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
