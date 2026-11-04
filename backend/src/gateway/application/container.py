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
from gateway.application.retention import Retention
from gateway.config import Settings
from gateway.domain.events import TENANT_OF_DEPLOYMENT
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.eventstore.postgres import PostgresEventStore
from gateway.infrastructure.eventstore.projections import ProjectionRunner, ReadModels
from gateway.log import log
from gateway.observability import Metrics, RequestRecorder
from gateway.proxy.health import Prober
from gateway.proxy.providers import close_client_storage, configure_client_storage
from gateway.proxy.router import GatewayRouter, LifespanTask
from gateway.proxy.runtime import build_limiter
from gateway.proxy.webhooks import Notifier


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
    metrics: Metrics = Field(default_factory=Metrics)
    limiter: Any
    #: Traffic retention. Built here so the boot job and the panel share one
    #: definition of "old".
    retention: Any = None
    #: Last upstream health per gateway. In-process: liveness is not an event.
    prober: Any = None
    #: Pushes observations to registered webhooks, off the request path.
    notifier: Any = None
    router: GatewayRouter = Field(default=None)  # type: ignore[assignment]
    #: The read-only console MCP, and the verifier in front of it. Built here so
    #: a test can call a tool without standing up an HTTP transport, and so there
    #: is exactly one of each per process.
    console: Any = None
    console_verifier: Any = None
    #: The console's ASGI app, built once. `http_app()` returns a *new* object on
    #: each call, and the one whose lifespan was entered is the one that can serve;
    #: building a second in the dispatcher gave every request "Task group is not
    #: initialized", which made the seeded `console` gateway a 500 upstream.
    console_app: Any = None
    console_lifespan: Any = None

    @classmethod
    async def create(cls, settings: Settings) -> Container:
        database = Database(settings.database_url)
        await database.migrate()

        # Before the router installs anything: a provider built without a backend
        # would hand out tokens nothing else can read.
        await configure_client_storage(
            settings.database_url, secret=settings.session_secret.get_secret_value()
        )

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
            commands=CommandHandler(
                store,
                projections,
                sealing_secret=settings.session_secret.get_secret_value(),
            ),
            recorder=recorder,
            limiter=build_limiter(settings.redis_url),
        )
        container.retention = Retention(database)
        container.prober = Prober()
        container.notifier = Notifier(
            database,
            read_models,
            sealing_secret=settings.session_secret.get_secret_value(),
        )
        await container.notifier.start()
        # The hook rides the recorder's own queue rather than a second one: the
        # observation already exists there, and a second ordering of the same
        # fact would let a delivery arrive before the row it describes.
        recorder.on_observation = container.notifier.enqueue
        container.router = GatewayRouter(
            read_models=read_models,
            limiter=container.limiter,
            metrics=container.metrics,
            recorder=recorder,
            base_url=settings.base_url,
            store=store,
            max_body_bytes=settings.max_body_bytes,
            trusted_proxies=settings.trusted_proxies,
        )
        from gateway.console import build_console

        container.console = build_console(
            container, settings.session_secret.get_secret_value()
        )
        container.console_verifier = container.console.auth
        # Built once, with its lifespan entered: FastMCP's streamable-HTTP app
        # refuses every request until the session manager's task group is up, and
        # `http_app()` hands back a new object each call, so the dispatcher must
        # serve *this* one. See `Container.console_app`.
        console_app = container.console.http_app(path="/mcp", stateless_http=True)
        console_lifespan = (
            LifespanTask(lambda: console_app.lifespan(console_app))
            if console_app.lifespan
            else None
        )
        if console_lifespan is not None:
            await console_lifespan.astart()
        container.console_app = console_app
        container.console_lifespan = console_lifespan
        await container.router.refresh()
        await bootstrap_admin(container)
        await adopt_legacy_tenants(container)
        # Imported here: the tenant service imports this module for the type.
        from gateway.application.services.tenants import backfill_console_gateways

        await backfill_console_gateways(container)
        # Retention runs at boot, not on a timer: a daily cron in the panel would
        # be a scheduler inside a web process, and a pod that restarts more than
        # daily would never reach it. Pruning is idempotent, so a boot per deploy
        # is a good enough clock.
        if settings.retention_days > 0:
            try:
                await container.retention.prune(settings.retention_days)
            except Exception as exc:
                # Losing a prune is a disk problem tomorrow, not a serving problem
                # now; refusing to boot over it would be the worse trade.
                log.error("retention.failed", error=str(exc))
        return container

    async def refresh_router(self) -> None:
        await self.router.refresh()

    async def close(self) -> None:
        await self.recorder.stop()
        if self.notifier is not None:
            await self.notifier.stop()
        await self.limiter.close()
        await self.router.shutdown()
        if self.console_lifespan is not None:
            await self.console_lifespan.astop()
        await close_client_storage()
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
    checkpoint = await store.checkpoint(projections.name)
    # A checkpoint is a promise that everything before it was applied. Wiping
    # `rm_*` breaks that promise while leaving the number in place, and then a
    # restart silently replays nothing — the read model stays empty and the only
    # symptom is a panel that looks like a fresh install. An empty read model
    # next to a non-zero checkpoint is exactly that state, so start from zero.
    if checkpoint and await read_models.is_empty():
        log.warning("projection.replay_from_zero", checkpoint=checkpoint)
        checkpoint = 0
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
    await container.commands.ensure_tenant(
        tenant_id=TENANT_OF_DEPLOYMENT, name="Default"
    )
    await container.commands.register_operator(
        email=settings.admin_email,
        password=settings.admin_password.get_secret_value(),
        is_admin=True,
        tenant_id=TENANT_OF_DEPLOYMENT,
    )


async def adopt_legacy_tenants(container: Container) -> None:
    """Give the pre-tenancy accounts a real tenant row.

    Their streams say nothing about tenants, which folds to
    `TENANT_OF_DEPLOYMENT`; but a tenant with no row has no join code, so nobody
    could be invited into the installation that has been running all along.
    Runs on every boot and is idempotent both ways: the backfill only touches
    rows that are still null, and `ensure_tenant` is a no-op once the stream
    exists.
    """
    adopted = await container.read_models.backfill_legacy_operators(
        TENANT_OF_DEPLOYMENT
    )
    if adopted:
        log.info(
            "tenancy.adopted_legacy_accounts",
            tenant_id=TENANT_OF_DEPLOYMENT,
            accounts=adopted,
        )
    if not adopted and await container.read_models.tenant(TENANT_OF_DEPLOYMENT):
        return
    await container.commands.ensure_tenant(
        tenant_id=TENANT_OF_DEPLOYMENT, name="Default"
    )


@contextlib.asynccontextmanager
async def lifespan_container(settings: Settings) -> Any:
    container = await Container.create(settings)
    try:
        yield container
    finally:
        await container.close()


__all__ = [
    "Container",
    "adopt_legacy_tenants",
    "bootstrap_admin",
    "lifespan_container",
    "replay_projections",
]
