"""Per-caller request budgets for gateway traffic.

Known ceiling (single instance): counters are in-process, so N replicas mean N x
the configured limit. Upgrade path: move this state to Redis behind the same two
methods — see the `ponytail:` note below.
"""

import asyncio
import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding-window counter per (gateway, subject).

    A service, not a data model: it owns a lock and mutable buckets, so a
    Pydantic wrapper would add validation to a hot path that has nothing to
    validate. `hits` and `lock` are private by convention, not by machinery.

    ponytail: in-process only — correct for one replica, `N x limit` at N.
    Swap the two methods for a Redis INCR/EXPIRE pair when you scale out.
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

    def sweep(self) -> None:
        """Drop empty buckets so a long-lived process does not leak memory."""
        for key in [k for k, v in self._hits.items() if not v]:
            del self._hits[key]
