"""The Redis limiter, against a real Redis.

An in-process fake would prove that the Lua script is called, not that it is
right — and the thing that can be wrong is the script: which score gets trimmed,
whether the budget is checked before or after the increment, whether two replicas
can both see the last slot. Only a real server answers those.
"""

import asyncio
import socket
import time

import pytest
from testcontainers.core.container import DockerContainer


@pytest.fixture(scope="module")
def redis_url():
    box = DockerContainer("redis:7-alpine").with_exposed_ports(6379)
    box.start()
    try:
        host = box.get_container_host_ip()
        port = int(box.get_exposed_port(6379))
        # `wait_for_logs` needs the `redis` package for its log strategy, so poll
        # with a real command instead. A TCP connect is not enough: the port is
        # open before the server answers, and a first `EVAL` that fails buys a
        # whole window of fail-open — the test would then assert on the fallback.
        for _ in range(100):
            probe = socket.socket()
            probe.settimeout(0.2)
            try:
                if probe.connect_ex((host, port)) == 0:
                    probe.sendall(b"PING\r\n")
                    if probe.recv(64).startswith(b"+PONG"):
                        break
            except OSError:
                pass
            finally:
                probe.close()
            time.sleep(0.1)
        yield f"redis://{host}:{port}"
    finally:
        box.stop()


def _allow(url: str, calls: list[tuple[str, str, int]]) -> list[bool]:
    from gateway.proxy.rate_limit import RedisLimiter

    async def run() -> list[bool]:
        limiter = RedisLimiter(url)
        try:
            return [await limiter.allow(*call) for call in calls]
        finally:
            await limiter.close()

    return asyncio.run(run())


def test_budget_is_shared_per_gateway_and_subject(redis_url) -> None:
    allowed = _allow(
        redis_url,
        [
            ("gw_shared", "alice", 2),
            ("gw_shared", "alice", 2),
            ("gw_shared", "alice", 2),
            # A different subject has its own budget, and so does another gateway.
            ("gw_shared", "bob", 2),
            ("gw_other", "alice", 2),
            # Zero is "unlimited", which must not consume anything.
            ("gw_shared", "alice", 0),
        ],
    )
    assert allowed == [True, True, False, True, True, True], allowed


def test_a_broken_redis_fails_open() -> None:
    """A cache that is down must not become an outage.

    The gateway already has auth in front of it; a limiter that cannot count is
    worth less than serving the traffic. One lost window, not two sources of
    truth disagreeing — which is why the in-process limiter is not consulted
    afterwards.
    """
    from gateway.proxy.rate_limit import RedisLimiter

    async def run() -> list[bool]:
        limiter = RedisLimiter("redis://127.0.0.1:1")
        try:
            return [await limiter.allow("gw", "s", 1) for _ in range(3)]
        finally:
            await limiter.close()

    assert asyncio.run(run()) == [True, True, True]
