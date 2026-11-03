"""Async engine and session factory — one place that knows how to reach the DB.

`create_async_engine` owns the pool, so nothing else in the app opens a
connection. That is what makes "no raw SQL" enforceable: the store and the
read models ask this module for a session and use the SQLAlchemy expression
language against `tables.metadata`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gateway.infrastructure.db import migrations
from gateway.log import log


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
        """Bring the schema to the current version.

        The steps live in `migrations.py`, in order, and each one runs once per
        database. Every boot calls this; a database already at the latest version
        does no work.
        """
        await migrations.run(self._engine)

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
