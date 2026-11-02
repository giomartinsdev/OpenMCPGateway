"""Async engine and session factory — one place that knows how to reach the DB.

`create_async_engine` owns the pool, so nothing else in the app opens a
connection. That is what makes "no raw SQL" enforceable: the store and the
read models ask this module for a session and use the SQLAlchemy expression
language against `tables.metadata`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gateway.infrastructure.db.tables import TENANT_OF_DEPLOYMENT, metadata
from gateway.log import log

# Columns added after their table shipped. `create_all` ignores existing tables,
# so an installation that upgraded needs these spelled out. Kept as plain SQL
# because `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` has no portable expression-
# language equivalent, and being unportable is fine: this module only talks to
# Postgres.
_ADDED_COLUMNS = (
    "ALTER TABLE rm_operators ADD COLUMN IF NOT EXISTS tenant_id TEXT",
    f"ALTER TABLE rm_gateways ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL "
    f"DEFAULT '{TENANT_OF_DEPLOYMENT}'",
    f"ALTER TABLE rm_roles ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL "
    f"DEFAULT '{TENANT_OF_DEPLOYMENT}'",
)


class Database:
    """Owns the engine, the session factory and the schema lifecycle."""

    def __init__(self, dsn: str, *, echo: bool = False,
                 pool_size: int = 10) -> None:
        self._engine: AsyncEngine = create_async_engine(
            as_async_dsn(dsn),
            echo=echo,
            pool_size=pool_size,
            max_overflow=10,
            # Verify a pooled connection before use: a pod that lost its network
            # for a moment should notice on checkout, not mid-transaction.
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A transaction-scoped session. Commit is the caller's decision."""
        async with self._sessionmaker() as session:
            yield session

    async def migrate(self) -> None:
        """Create anything missing, then add columns to tables that predate them.

        `create_all` only creates whole tables: it will not add a column to a
        table that already exists, which is exactly the upgrade case for an
        installation that has been running. Two `ADD COLUMN IF NOT EXISTS` lines
        are cheaper than a migration tool, and idempotent, so every boot runs
        them. A real schema history is the upgrade path when a change needs a
        backfill, not just a new nullable column.
        """
        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            for statement in _ADDED_COLUMNS:
                await connection.execute(text(statement))
        log.info("database.migrated", tables=len(metadata.tables))

    async def close(self) -> None:
        # `dispose` closes the pool without waiting for graceful per-connection
        # teardown. On shutdown a connection can be parked mid-statement by a
        # cancelled request, and waiting for it means SIGKILL instead of a clean
        # exit. Every committed transaction is already durable.
        await self._engine.dispose()
        log.info("database.closed")


def as_async_dsn(dsn: str) -> str:
    """Normalise a plain `postgresql://` DSN to the asyncpg driver.

    Accepting both spellings means a user can paste the URL their database
    provider gave them without knowing which driver this app uses.
    """
    if "+asyncpg" in dsn:
        return dsn
    # testcontainers hands back the psycopg2 spelling; normalise both.
    for prefix in ("postgresql+psycopg2://", "postgres://", "postgresql://"):
        if dsn.startswith(prefix):
            return dsn.replace(prefix, "postgresql+asyncpg://", 1)
    return dsn


__all__ = ["Database", "as_async_dsn"]
