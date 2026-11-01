"""Request recorder — writes traffic observations as events, off the hot path.

The proxy must not block on bookkeeping, and a stats write failure must never
fail a user's MCP call. Both concerns are handled here: `record` enqueues and
returns, a background task drains the queue, and full/closed queues drop
observations rather than propagating errors upward.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from gateway.domain.events import RequestObserved
from gateway.infrastructure.eventstore.postgres import PostgresEventStore
from gateway.infrastructure.eventstore.projections import ProjectionRunner


@dataclass(slots=True)
class _Observation:
    gateway_id: str
    subject: str
    outcome: str
    status: int
    duration_ms: int
    ip: str | None
    tool: str | None
    method: str


class RequestRecorder:
    """Bounded queue + writer task for `request.observed` events."""

    def __init__(self, store: PostgresEventStore, projections: ProjectionRunner,
                 *, max_queue: int = 10_000) -> None:
        self._store = store
        self._projections = projections
        self._queue: asyncio.Queue[_Observation] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._drain(), name="request-recorder")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def record(self, *, gateway_id: str, subject: str, outcome: str,
                     status: int, duration_ms: int, ip: str | None,
                     tool: str | None, method: str) -> None:
        observation = _Observation(
            gateway_id=gateway_id,
            subject=subject,
            outcome=outcome,
            status=status,
            duration_ms=duration_ms,
            ip=ip,
            tool=tool,
            method=method,
        )
        try:
            self._queue.put_nowait(observation)
        except asyncio.QueueFull:
            # Shedding observations beats queueing unbounded or blocking a call.
            self._dropped += 1

    @property
    def dropped(self) -> int:
        return self._dropped

    async def _drain(self) -> None:
        while True:
            observation = await self._queue.get()
            try:
                await self._write(observation)
            except Exception:  # noqa: BLE001 - bookkeeping must not kill the loop
                self._dropped += 1
            finally:
                self._queue.task_done()

    async def _write(self, observation: _Observation) -> None:
        # Observations are their own tiny aggregate: they need no version check
        # and must never contend with gateway streams.
        event = RequestObserved(
            aggregate_id=f"traffic_{observation.gateway_id}",
            gateway_id=observation.gateway_id,
            subject=observation.subject,
            outcome=observation.outcome,
            status=observation.status,
            duration_ms=observation.duration_ms,
            ip=observation.ip,
            tool=observation.tool,
            method=observation.method,
        )
        await self._store.append_unversioned([event], actor="proxy")
        await self._projections.apply(0, event)


__all__ = ["RequestRecorder"]
