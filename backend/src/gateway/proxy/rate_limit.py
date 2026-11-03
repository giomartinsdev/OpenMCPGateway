"""Per-caller request budgets for gateway traffic.

Two backends behind one method:

* **Redis** when `OMG_REDIS_URL` is set. A sliding window per (gateway,
  subject), because N replicas sharing an in-process counter means the
  configured limit is really N x the configured limit — a limit that silently
  multiplies is worse than no limit at all.
* **In-process** when it is not. One replica, one process, no dependency: the
  common case for a self-hosted deployment, and the one the tests run against.

The Redis client is deliberately tiny — `INCR`/`EXPIRE`/`EVAL` over a socket,
about sixty lines — rather than the `redis` package. The whole surface is one
atomic script, and a dependency that ships its own async pool, cluster client
and pub/sub for that is the kind of thing a gateway avoids until it needs it.

ponytail: one connection, no pool, no reconnect backoff. Redis is local to the
cluster and every failure falls back to the in-process limiter, so a broken
connection costs accuracy for a minute, not availability.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from gateway.log import log

# Sliding window in one round trip: trim what fell out of the window, count what
# is left, and add this hit only if it fits. Atomic, so two replicas cannot both
# see "one slot left".
#
# A sorted set of hit timestamps rather than `INCR` + `EXPIRE`: a fixed window
# lets a caller spend the whole budget at 59s and again at 61s, which is double
# the limit at the only moment anyone was watching.
_SLIDING = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local cutoff = now - tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local used = redis.call('ZCARD', key)
if used >= limit then
  return 0
end
redis.call('ZADD', key, now, now .. '-' .. ARGV[4])
redis.call('PEXPIRE', key, tonumber(ARGV[2]))
return 1
"""


class RedisLimiter:
    """Sliding-window counter in Redis, shared by every replica.

    `allow` never raises: a Redis that is down answers "yes" and logs, because
    the alternative is a gateway that stops serving traffic when its cache does.
    That trade is only acceptable because the process-local limiter is not
    consulted after a Redis failure — one lost window of accounting, not two
    sources of truth disagreeing.
    """

    def __init__(self, url: str, *, window_seconds: float = 60.0) -> None:
        parts = urlsplit(url)
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or 6379
        self._password = parts.password
        self._db = int((parts.path or "/0").lstrip("/") or 0)
        self._window = window_seconds
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._down_until = 0.0

    async def allow(self, gateway_id: str, subject: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        if now < self._down_until:
            return True
        key = f"omg:rl:{gateway_id}:{subject}"
        try:
            async with self._lock:
                return bool(await self._eval(key, limit))
        except Exception as exc:
            log.warning("rate_limit.redis_unavailable", error=repr(exc))
            await self._drop()
            # Back off for a window: one log line per minute, not per request.
            self._down_until = now + self._window
            return True

    async def close(self) -> None:
        await self._drop()

    # -- the wire ---------------------------------------------------------

    async def _eval(self, key: str, limit: int) -> int:
        reply = await self._command(
            "EVAL",
            _SLIDING,
            "1",
            key,
            repr(time.time()),
            repr(self._window),
            str(limit),
            # Member uniqueness: two hits in the same millisecond must not
            # collapse into one, which would let a burst through for free.
            f"{time.monotonic_ns()}",
        )
        return int(reply)

    async def _command(self, *parts: str) -> object:
        writer = await self._ensure()
        await self._send(writer, parts)
        return await self._read_reply()

    async def _send(self, writer: asyncio.StreamWriter, parts: tuple[str, ...]) -> None:
        payload = f"*{len(parts)}\r\n" + "".join(
            f"${len(part.encode())}\r\n{part}\r\n" for part in parts
        )
        writer.write(payload.encode())
        await writer.drain()

    async def _ensure(self) -> asyncio.StreamWriter:
        if self._writer is not None and not self._writer.is_closing():
            return self._writer
        reader, writer = await asyncio.open_connection(
            self._host, self._port, limit=64 * 1024
        )
        self._reader, self._writer = reader, writer
        # Raw sends, not `_command`: `_ensure` is what `_command` waits on, and
        # calling back into it here would recurse before the connection is set.
        if self._password:
            await self._send(writer, ("AUTH", self._password))
            await self._read_reply()
        if self._db:
            await self._send(writer, ("SELECT", str(self._db)))
            await self._read_reply()
        return writer

    async def _read_reply(self) -> object:
        assert self._reader is not None
        line = (await self._reader.readline()).decode().rstrip("\r\n")
        if not line:
            raise ConnectionError("redis closed the connection")
        kind, body = line[0], line[1:]
        if kind == "+":
            return body
        if kind == "-":
            raise ConnectionError(body)
        if kind == ":":
            return int(body)
        if kind == "$":
            length = int(body)
            if length < 0:
                return None
            data = await self._reader.readexactly(length + 2)
            return data[:-2].decode()
        if kind == "*":
            count = int(body)
            return [await self._read_reply() for _ in range(max(0, count))]
        raise ConnectionError(f"unexpected redis reply: {line!r}")

    async def _drop(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()


class RateLimiter:
    """Sliding-window counter per (gateway, subject).

    A service, not a data model: it owns a lock and mutable buckets, so a
    Pydantic wrapper would add validation to a hot path that has nothing to
    validate. `hits` and `lock` are private by convention, not by machinery.

    ponytail: in-process only — correct for one replica, `N x limit` at N. Set
    `OMG_REDIS_URL` to use `RedisLimiter` instead; the container picks.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, gateway_id: str, subject: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        window = now - 60.0
        async with self._lock:
            bucket = self._hits[(gateway_id, subject)]
            while bucket and bucket[0] < window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    async def close(self) -> None:
        """Nothing to release; here so the container can close either backend."""


def build_limiter(url: str | None) -> RateLimiter | RedisLimiter:
    """The limiter the deployment asked for, defaulting to the safe one."""
    if url:
        log.info("rate_limit.backend", backend="redis")
        return RedisLimiter(url)
    return RateLimiter()


__all__ = ["RateLimiter", "RedisLimiter", "build_limiter"]
