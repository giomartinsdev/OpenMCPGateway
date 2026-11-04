"""Request recorder — writes traffic observations as events, off the hot path.

The proxy must not block on bookkeeping, and a stats write failure must never
fail a user's MCP call. Both concerns are handled here: `record` enqueues and
returns, a background task drains the queue, and a full queue drops observations
rather than propagating errors upward.
"""

import asyncio
import contextlib
from collections import defaultdict
from typing import Any

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
    #: Why a refused call was refused. Empty for an allowed one.
    reason: str = ""
    #: The id the caller sent, or the one the guard minted for them.
    request_id: str = ""
    #: The payload trace: exact byte counts, and a redacted prefix of each body.
    #: Sized here rather than in the projection because the guard is the only
    #: place that sees both bodies.
    bytes_in: int = 0
    bytes_out: int = 0
    request_preview: str = ""
    response_preview: str = ""
    #: The public name, for the webhook payload: a receiver subscribing to
    #: `gateway_down` knows slugs, not ids.
    gateway_slug: str = ""


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
        #: Set by the container when webhooks are configured. Called for every
        #: observation, before the write: a hook that waits for a database round
        #: trip is a hook that is late.
        self.on_observation: Any = None

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
        if self.on_observation is not None:
            self.on_observation(observation)
        # Observations are their own tiny aggregate: they need no version check
        # and must never contend with gateway streams.
        event = RequestObserved(
            aggregate_id=f"traffic_{observation.gateway_id}",
            # `gateway_slug` is a presentation field for the webhook payload; the
            # event names the gateway by id, like every other event does.
            **observation.model_dump(exclude={"gateway_slug"}),
        )
        await self._store.append_unversioned([event], actor="proxy")
        await self._projections.apply(0, event)


class Metrics:
    """Request counters in the Prometheus text format.

    In-process, like the default rate limiter, and for the same reason: the
    alternative is a client library and a push gateway for six counters. With one
    replica (the documented default) nothing is lost; with several, each pod is a
    series and `sum by (gateway)` in the query is the correct aggregation anyway.

    Labels are deliberately low-cardinality: gateway slug, outcome, status class.
    A label per caller or per IP would turn a metrics endpoint into a second,
    worse traffic log.
    """

    #: Request durations, in milliseconds, summed per (gateway, outcome). A
    #: summary without quantiles: `rate(sum) / rate(count)` is the mean, and that
    #: is the number an operator actually acts on. Buckets are the traffic log's
    #: job, and it already has them per minute.
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._duration_ms: dict[tuple[str, str], int] = defaultdict(int)
        self._dropped = 0

    def observe(
        self, *, gateway: str, outcome: str, status: int, duration_ms: int
    ) -> None:
        self._counts[(gateway, outcome, _status_class(status))] += 1
        self._duration_ms[(gateway, outcome)] += max(0, duration_ms)

    def dropped(self, count: int) -> None:
        """Mirror the recorder's counter. Called before every render rather than
        kept in sync by the recorder, so the two cannot disagree."""
        self._dropped = count

    def render(self, *, live: int, pending: int, limit_backend: str) -> str:
        """The exposition body. Gauges are supplied by the caller: this object
        knows what it was told, not what the router currently serves."""
        lines: list[str] = [
            "# HELP omg_proxy_requests_total MCP requests by gateway, outcome and status class.",
            "# TYPE omg_proxy_requests_total counter",
        ]
        for (gateway, outcome, klass), count in sorted(self._counts.items()):
            lines.append(
                f'omg_proxy_requests_total{{gateway="{_escape(gateway)}",'
                f'outcome="{_escape(outcome)}",status="{klass}"}} {count}'
            )
        lines.extend([
            "# HELP omg_proxy_request_duration_ms_sum Total time spent in proxied requests.",
            "# TYPE omg_proxy_request_duration_ms_sum counter",
        ])
        for (gateway, outcome), total in sorted(self._duration_ms.items()):
            lines.append(
                f'omg_proxy_request_duration_ms_sum{{gateway="{_escape(gateway)}",'
                f'outcome="{_escape(outcome)}"}} {total}'
            )
        lines.extend([
            "# HELP omg_gateways_live Gateways currently serving.",
            "# TYPE omg_gateways_live gauge",
            f"omg_gateways_live {live}",
            "# HELP omg_gateways_pending Enabled gateways not serving, and why (see /healthz).",
            "# TYPE omg_gateways_pending gauge",
            f"omg_gateways_pending {pending}",
            "# HELP omg_traffic_observations_dropped Observations dropped before reaching the log.",
            "# TYPE omg_traffic_observations_dropped counter",
            f"omg_traffic_observations_dropped {self._dropped}",
            "# HELP omg_rate_limit_backend 1 for the shared (Redis) limiter, 0 for in-process.",
            "# TYPE omg_rate_limit_backend gauge",
            f"omg_rate_limit_backend {1 if limit_backend == 'redis' else 0}",
        ])
        return "\n".join(lines) + "\n"


def _status_class(status: int) -> str:
    """`4xx`, not `429`: a label per status is a series per status."""
    return f"{status // 100}xx" if 100 <= status < 600 else "other"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


__all__ = ["Metrics", "Observation", "RequestRecorder"]
