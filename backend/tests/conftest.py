"""Fixtures: a real Postgres, a real ASGI app, and one event loop for all of it.

Two deliberate choices:

* **No mocks for storage.** Event-sourcing bugs hide precisely where mocks are
  permissive — transactions, unique constraints, JSONB round-trips. Every
  scenario runs against a real `postgres:17` container.
* **A single background event loop.** pytest-bdd steps are synchronous, but
  SQLAlchemy engines and ASGI apps are bound to the loop they were created on.
  `asyncio.run` per step would create a new loop each time and break the pool,
  so all async work is submitted to one thread-owned loop via `run()`.
"""

import asyncio
import contextlib
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest
from testcontainers.community.postgres import PostgresContainer

from gateway.application.container import Container
from gateway.config import Settings

T = TypeVar("T")


class LoopRunner:
    """Owns one event loop on a background thread; `run()` blocks until done."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._serve, name="test-loop", daemon=True
        )
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], timeout: float = 60) -> T:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


@pytest.fixture(scope="session")
def runner():
    loop_runner = LoopRunner()
    try:
        yield loop_runner
    finally:
        loop_runner.stop()


@pytest.fixture(scope="session")
def postgres():
    container = PostgresContainer("postgres:17-alpine")
    container.start()
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def base_url() -> str:
    return "https://gateway.test"


@pytest.fixture(scope="session")
def settings(postgres, base_url) -> Settings:
    return Settings(
        database_url=postgres,
        session_secret="test-secret-key-at-least-32-bytes-long!!",
        base_url=base_url,
    )


@pytest.fixture()
def container(runner, settings) -> Container:
    instance = runner.run(Container.create(settings))
    try:
        yield instance
    finally:
        runner.run(_stop_servers())
        runner.run(instance.close())


@pytest.fixture(autouse=True)
def clean_database(runner, postgres):
    """Each scenario starts from an empty log.

    Scenarios reuse slugs, so state must not leak between them. Truncating is the
    honest option: replaying a shared log would let one scenario's events decide
    another's outcome.
    """
    yield
    runner.run(_truncate(postgres))


async def _truncate(dsn: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from gateway.infrastructure.db.database import as_async_dsn

    engine = create_async_engine(as_async_dsn(dsn))
    try:
        async with engine.begin() as connection:
            # Discovered, not hard-coded: a migration test can leave the database
            # in the middle of an upgrade — a schema dropped, a table still in
            # `public` — and truncating a name that does not exist is an error
            # that hides the test's own failure.
            rows = (
                await connection.execute(
                    text(
                        "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables "
                        "WHERE schemaname IN ('public', 'readmodels') "
                        "AND tablename <> 'schema_migrations'"
                    )
                )
            ).scalars().all()
            if not rows:
                return
            await connection.execute(
                text(f"TRUNCATE {', '.join(rows)} RESTART IDENTITY CASCADE")
            )
    finally:
        await engine.dispose()


@pytest.fixture()
def live_server(runner, container) -> str:
    """Serve the real ASGI app on a random port; yield its base URL.

    The app is started on the background loop so requests and the recorder task
    share the container's loop.
    """
    return runner.run(_start_server(container))


async def _start_server(container: Container) -> str:
    import socket

    import uvicorn

    from gateway.interface.app import GatewayDispatcher, create_admin_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    admin = create_admin_app(container.settings)
    admin.state.container = container
    app = GatewayDispatcher(admin)

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning",
            # Same as `omg serve`: the guard owns the forwarded-address decision,
            # so a test that forges the header must be refused for the same reason
            # production refuses it.
            proxy_headers=False,
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"

    _register_shutdown(container, server, task)
    return f"http://127.0.0.1:{port}"


_SERVERS: list[tuple[Any, Any]] = []


def _register_shutdown(container: Container, server: Any, task: Any) -> None:
    """Record the server so the container fixture can stop it before closing.

    `Container` is a slots dataclass, so its methods cannot be patched; a
    module-level registry keeps the teardown order explicit instead.
    """
    del container
    _SERVERS.append((server, task))


async def _stop_servers() -> None:
    while _SERVERS:
        server, task = _SERVERS.pop()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await task
