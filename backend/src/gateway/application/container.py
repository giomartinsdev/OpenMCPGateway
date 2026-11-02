"""Shared app wiring: build every collaborator once, hand them to both surfaces.

The admin API and the proxy runtime must share one `RateLimiter`, one recorder
and one `ReadModels` — otherwise a config change made in the UI would not be
visible to traffic. This module is the single place that wiring lives.

Collaborators take a `Database`, not a pool: nothing outside this module knows
how the app reaches Postgres.
"""

import contextlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gateway.application.commands import CommandHandler
from gateway.application.ports import EventStore
from gateway.config import Settings
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.eventstore.postgres import PostgresEventStore
from gateway.infrastructure.eventstore.projections import ProjectionRunner, ReadModels
from gateway.observability import RequestRecorder
from gateway.proxy.router import GatewayRouter
from gateway.proxy.runtime import RateLimiter


class Container(BaseModel):
    """Every collaborator, built once. Wiring changes happen here and nowhere else."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings
    database: Database
    store: EventStore
    projections: ProjectionRunner
    read_models: ReadModels
    commands: CommandHandler
    recorder: RequestRecorder
    limiter: RateLimiter
    router: GatewayRouter = Field(default=None)  # type: ignore[assignment]

    @classmethod
    async def create(cls, settings: Settings) -> Container:
        database = Database(settings.database_url)
        await database.migrate()

        store = PostgresEventStore(database)
        projections = ProjectionRunner(database)
        read_models = ReadModels(database)
        await replay_projections(store, projections, read_models)

        recorder = RequestRecorder(store, projections)
        await recorder.start()

        container = cls(
            settings=settings,
            database=database,
            store=store,
            projections=projections,
            read_models=read_models,
            commands=CommandHandler(store, projections),
            recorder=recorder,
            limiter=RateLimiter(),
        )
        container.router = GatewayRouter(
            read_models=read_models,
            limiter=container.limiter,
            recorder=recorder,
            base_url=settings.base_url,
            store=store,
        )
        await container.router.refresh()
        await bootstrap_admin(container)
        return container

    async def refresh_router(self) -> None:
        await self.router.refresh()

    async def close(self) -> None:
        await self.recorder.stop()
        await self.router.shutdown()
        await self.database.close()


async def replay_projections(
    store: EventStore,
    projections: ProjectionRunner,
    read_models: ReadModels,
) -> None:
    """Catch read models up with the log on boot.

    Projections are applied synchronously on write, so this normally does
    nothing; it exists so a wiped read model (or a second instance starting
    later) is rebuilt from the log rather than serving stale/empty data.
    """
    del read_models
    checkpoint = await store.checkpoint(projections.name)
    replayed = 0
    async for global_seq, event in store.read_all(since=checkpoint):
        await projections.apply(global_seq, event)
        checkpoint = global_seq
        replayed += 1
    if replayed:
        await store.save_checkpoint(projections.name, checkpoint)


async def bootstrap_admin(container: Container) -> None:
    """Create the first operator from env vars, once.

    Without this a fresh deployment has no way in: the UI requires a login and
    there is no user yet. Idempotent — a second boot is a no-op.
    """
    settings = container.settings
    if not settings.admin_email or not settings.admin_password:
        return
    existing = await container.store.fetch_operator_by_email(settings.admin_email)
    if existing:
        return
    await container.commands.register_operator(
        email=settings.admin_email,
        password=settings.admin_password.get_secret_value(),
        is_admin=True,
    )


@contextlib.asynccontextmanager
async def lifespan_container(settings: Settings) -> Any:
    container = await Container.create(settings)
    try:
        yield container
    finally:
        await container.close()


__all__ = ["Container", "bootstrap_admin", "lifespan_container", "replay_projections"]
