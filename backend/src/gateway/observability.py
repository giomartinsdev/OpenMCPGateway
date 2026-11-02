"""Request recorder — writes traffic observations as events, off the hot path.

The proxy must not block on bookkeeping, and a stats write failure must never
fail a user's MCP call. Both concerns are handled here: `record` enqueues and
returns, a background task drains the queue, and a full queue drops observations
rather than propagating errors upward.
"""

import asyncio
import contextlib

from pydantic import BaseModel, ConfigDict

from gateway.application.ports import EventStore
from gateway.domain.events import RequestObserved
from gateway.infrastructure.eventstore.projections import ProjectionRunner
from gateway.log import log


class Observation(BaseModel):
    """One call as the proxy saw it. Immutable: it describes the past."""

    model_config = ConfigDict(frozen=True)

    gateway_id: str
    subject: str
    outcome: str
    status: int
    duration_ms: int
    ip: str | None = None
    tool: str | None = None
    method: str = "POST"


class RequestRecorder:
    """Bounded queue + writer task for `request.observed` events."""

    def __init__(
        self,
        store: EventStore,
        projections: ProjectionRunner,
        *,
        max_queue: int = 10_000,
    ) -> None:
        self._store = store
        self._projections = projections
        self._queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize=max_queue)
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

    async def record(self, **fields: object) -> None:
        observation = Observation(**fields)  # type: ignore[arg-type]
        try:
            self._queue.put_nowait(observation)
        except asyncio.QueueFull:
            # Shedding observations beats queueing unbounded or blocking a call.
            self._dropped += 1
            log.warning(
                "traffic.observation_dropped",
                gateway_id=observation.gateway_id,
                dropped_total=self._dropped,
            )

    @property
    def dropped(self) -> int:
        return self._dropped

    async def _drain(self) -> None:
        while True:
            observation = await self._queue.get()
            try:
                await self._write(observation)
            except Exception as exc:
                self._dropped += 1
                # The caller already got its response; losing a statistic is the
                # correct outcome, and saying so is the only way to see it.
                log.error(
                    "traffic.write_failed",
                    gateway_id=observation.gateway_id,
                    error=str(exc),
                    dropped_total=self._dropped,
                )
            finally:
                self._queue.task_done()

    async def _write(self, observation: Observation) -> None:
        # Observations are their own tiny aggregate: they need no version check
        # and must never contend with gateway streams.
        event = RequestObserved(
            aggregate_id=f"traffic_{observation.gateway_id}",
            **observation.model_dump(),
        )
        await self._store.append_unversioned([event], actor="proxy")
        await self._projections.apply(0, event)


__all__ = ["Observation", "RequestRecorder"]
