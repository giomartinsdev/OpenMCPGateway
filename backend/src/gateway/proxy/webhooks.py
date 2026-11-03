"""Webhooks: the same observation the panel shows, pushed to whoever asked.

A hook is a URL plus a secret. The POST carries the observation as JSON and an
`X-OMG-Signature: <hex hmac-sha256>` over the exact body, so a receiver can tell
our POST from anyone else's without knowing anything but the secret.

Two deliberate limits:

* Delivery happens off the request path. The guard already records traffic
  asynchronously; the hook rides the same queue, so a receiver that takes ten
  seconds never slows an MCP call.
* Retries are bounded and in-process. A pod that dies mid-retry loses the
  attempt, and the delivery is left as whatever the last write said. That is
  honest for a webhook: the alternative is a durable outbox with its own
  consumer, which this deployment does not have.

ponytail: in-flight retries do not survive a restart. Add a durable outbox table
with a claimed-at column when a missed alert becomes unacceptable.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gateway.domain.events import RequestObserved
from gateway.infrastructure.db.tables import rm_webhook_deliveries
from gateway.infrastructure.security import open_sealed
from gateway.log import log
from gateway.observability import Observation

#: Attempts per delivery. Three is the smallest number that distinguishes "down"
#: from "blinked", and the backoff stays under a minute so a hook is not silent
#: for longer than a reader would wait before asking.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.2, 1.0)
TIMEOUT_SECONDS = 5.0


class Notifier:
    """A bounded queue and a task that signs and POSTs observations.

    The queue is bounded for the same reason the recorder's is: a receiver that
    stops reading must not grow the process until the pod is killed. Dropping an
    alert is bad; dropping the whole gateway is worse.
    """

    def __init__(self, database: Any, read_models: Any, *,
                 sealing_secret: str, max_queue: int = 1_000) -> None:
        self._db = database
        self._read_models = read_models
        self._secret = sealing_secret
        self._queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._dropped = 0

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._task = asyncio.create_task(self._drain(), name="webhook-notifier")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def enqueue(self, observation: Observation) -> None:
        try:
            self._queue.put_nowait(observation)
        except asyncio.QueueFull:
            self._dropped += 1
            log.warning("webhook.dropped", gateway_id=observation.gateway_id)

    @property
    def dropped(self) -> int:
        return self._dropped

    async def _drain(self) -> None:
        while True:
            observation = await self._queue.get()
            try:
                await self.deliver(observation)
            except Exception as exc:  # never let one hook kill the loop
                log.error("webhook.delivery_failed", error=repr(exc))
            finally:
                self._queue.task_done()

    async def deliver(self, observation: Observation) -> None:
        """Send the observation to every hook that asked for this outcome."""
        try:
            hooks = await self._read_models.webhook_targets(
                observation.gateway_id, observation.outcome
            )
        except Exception as exc:
            log.error("webhook.lookup_failed", error=repr(exc))
            return
        if not hooks:
            return
        body = json.dumps(_payload(observation), sort_keys=True).encode()
        for webhook_id, url, sealed in hooks:
            await self._post(webhook_id, url, sealed, observation, body)

    async def _post(self, webhook_id: str, url: str, sealed: str,
                    observation: Observation, body: bytes) -> None:
        assert self._client is not None
        opened = open_sealed(sealed, secret=self._secret)
        if opened is None:
            # A secret that cannot be opened is a deployment change (a rotated
            # `OMG_SESSION_SECRET`), not a receiver failure; record it once
            # instead of retrying three times against a signature we cannot make.
            log.error("webhook.secret_unreadable", webhook_id=webhook_id)
            await self._record(webhook_id, observation, "failed", 0, None,
                               "secret unreadable")
            return
        secret = opened.encode()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-OMG-Signature": signature,
            "X-OMG-Outcome": observation.outcome,
        }

        last_status: int | None = None
        error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.post(url, content=body, headers=headers)
                last_status = response.status_code
                if response.status_code < 400:
                    await self._record(webhook_id, observation, "delivered",
                                       attempt, last_status, "")
                    return
                error = f"HTTP {response.status_code}"
            except Exception as exc:
                error = repr(exc)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
        await self._record(webhook_id, observation, "failed", MAX_ATTEMPTS,
                           last_status, error)

    async def _record(self, webhook_id: str, observation: Observation,
                      status: str, attempts: int, status_code: int | None,
                      error: str) -> None:
        """One row per delivery, because "the hook is silent" has two causes.

        No matching event, or every POST failing. Only a stored attempt tells
        them apart without reading logs.
        """
        try:
            async with self._db.session() as session, session.begin():
                await session.execute(
                    pg_insert(rm_webhook_deliveries)
                    .values(
                        id=f"whd_{secrets.token_hex(8)}",
                        webhook_id=webhook_id,
                        event=observation.outcome,
                        status=status,
                        attempts=attempts,
                        status_code=status_code,
                        error=error,
                    )
                    .on_conflict_do_nothing(index_elements=[rm_webhook_deliveries.c.id])
                )
        except Exception as exc:
            log.error("webhook.record_failed", webhook_id=webhook_id, error=repr(exc))


def _payload(observation: Observation) -> dict[str, Any]:
    """What the receiver gets. The gateway is named by slug elsewhere; here it
    is the id the caller already uses in every API call."""
    return {
        "gateway_id": observation.gateway_id,
        "gateway": observation.gateway_slug,
        "outcome": observation.outcome,
        "status": observation.status,
        "subject": observation.subject,
        "ip": observation.ip,
        "tool": observation.tool,
        "reason": observation.reason,
        "request_id": observation.request_id,
        "duration_ms": observation.duration_ms,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


#: Kept because `RequestObserved` is what `_payload` conceptually describes; the
#: import proves the two names cannot drift apart silently.
_EVENT = RequestObserved
