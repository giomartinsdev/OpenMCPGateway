"""Upstream health: is the thing behind the gateway answering?

The panel could only answer "is the gateway up", which is `/healthz` and says
nothing about the MCP server on the other side of the proxy. A dead upstream
looked exactly like a healthy gateway nobody was calling.

`Prober` times a real MCP handshake — `list_tools`, not a TCP connect, because a
server that accepts the socket and then fails `initialize` is down for every
caller. Results are cached per gateway inside the process: "is it up right now"
is not an event, and an event per poll would fill the log with facts nobody
changed.
"""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

if TYPE_CHECKING:
    from gateway.application.container import Container

class Health(BaseModel):
    """The last thing we learned about a gateway's upstream."""

    model_config = ConfigDict(frozen=True)

    up: bool
    detail: str = ""
    tools: int = 0
    #: Tool names whose description or `inputSchema` differs from the last probe.
    #: A tool that disappeared is visible in the catalog; one that quietly changed
    #: shape is not, and that is the one that breaks a client that worked
    #: yesterday. Empty on the first probe: a baseline reported as drift would
    #: make the badge red on every fresh deployment.
    changed: list[str] = []
    checked_at: datetime

class Prober:
    """Probes upstreams, remembers the last answer, and does not stampede.

    A gateway's page polls every ten seconds; probing on each poll would turn one
    operator reading a page into a health-check loop against a production MCP
    server. The cache is refreshed on an explicit test or after `ttl`.
    """

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, Health] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def last(self, gateway_id: str) -> Health | None:
        return self._cache.get(gateway_id)

    async def probe(self, container: Container, gateway: dict[str, Any],
                    *, fresh: bool = True) -> Health:
        gateway_id = gateway["id"]
        cached = self._cache.get(gateway_id)
        if cached is not None and not fresh and _fresh(cached, self._ttl):
            return cached
        lock = self._locks.setdefault(gateway_id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(gateway_id)
            if cached is not None and not fresh and _fresh(cached, self._ttl):
                return cached
            result = await _probe(container, gateway)
            self._cache[gateway_id] = result
            return result

def _fresh(health: Health, ttl: float) -> bool:
    return (datetime.now(UTC) - health.checked_at).total_seconds() < ttl

async def _probe(container: Container, gateway: dict[str, Any]) -> Health:
    from fastmcp.client import Client

    events = await container.store.load_stream(gateway["id"])
    upstream_token = ""
    for event in events:
        upstream_token = event.payload().get("upstream_token") or upstream_token

    config: dict[str, Any] = {"mcpServers": {
        gateway["slug"]: {
            "url": gateway["target_url"],
            "transport": "http",
            **({"headers": {"Authorization": f"Bearer {upstream_token}"}}
               if upstream_token else {}),
        }
    }}

    started = time.perf_counter()
    try:
        async with Client(config, timeout=8.0) as client:
            tools = await client.list_tools()
    except Exception as exc:
        return Health(up=False, detail=_reason(exc), checked_at=datetime.now(UTC))
    changed = await _record_fingerprints(container, gateway["id"], tools)
    return Health(
        up=True,
        detail=f"{len(tools)} tools in {int((time.perf_counter() - started) * 1000)} ms",
        tools=len(tools),
        changed=changed,
        checked_at=datetime.now(UTC),
    )


async def _record_fingerprints(
    container: Container, gateway_id: str, tools: Any
) -> list[str]:
    """Compare the upstream catalog to the last one we saw, and remember this one.

    The fingerprint covers the parts a caller depends on — name, description and
    the input schema — because those are what a generated client compiles
    against. Comparing the whole tool object would flag `title` changes nobody
    parses; comparing nothing would miss the rename of a parameter.

    Stored in a read model, not in this process: a pod that restarts has not
    "just seen" the upstream, and would report every tool as new.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from gateway.infrastructure.db.tables import rm_tool_fingerprints

    current: dict[str, str] = {}
    for tool in tools:
        raw = {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "inputSchema": getattr(tool, "inputSchema", None),
        }
        current[raw["name"]] = hashlib.sha256(
            json.dumps(raw, sort_keys=True, default=str).encode()
        ).hexdigest()

    async with container.database.session() as session, session.begin():
        rows = (
            await session.execute(
                select(
                    rm_tool_fingerprints.c.tool, rm_tool_fingerprints.c.fingerprint
                ).where(rm_tool_fingerprints.c.gateway_id == gateway_id)
            )
        ).all()
        known = {row[0]: row[1] for row in rows}
        changed = [
            name for name, digest in current.items()
            if name in known and known[name] != digest
        ]
        # Forget tools the upstream no longer serves, so a name that comes back
        # later is compared against the catalog it returns with, not a stale row.
        gone = set(known) - set(current)
        if gone:
            await session.execute(
                sql_delete(rm_tool_fingerprints).where(
                    (rm_tool_fingerprints.c.gateway_id == gateway_id)
                    & rm_tool_fingerprints.c.tool.in_(gone)
                )
            )
        for name, digest in current.items():
            statement = pg_insert(rm_tool_fingerprints).values(
                gateway_id=gateway_id, tool=name, fingerprint=digest
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        rm_tool_fingerprints.c.gateway_id,
                        rm_tool_fingerprints.c.tool,
                    ],
                    set_={"fingerprint": statement.excluded.fingerprint},
                )
            )
    return sorted(changed)

def _reason(exc: Exception) -> str:
    """One readable line. A repr of an exception is a stack trace in a badge."""
    text = str(exc).strip() or exc.__class__.__name__
    return text.splitlines()[0][:300]

__all__ = ["Health", "Prober"]
