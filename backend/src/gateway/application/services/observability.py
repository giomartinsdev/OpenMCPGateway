"""Traffic and the audit trail, scoped to the caller's tenant.

The audit log lives in one stream table shared by every tenant, so "the events"
is not a query until you say whose. `owned_aggregates` is that answer: the tenant
itself, its gateways, its accounts. Without it the page is a data leak with a
search box.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.domain.readmodels import TrafficStats


def window(minutes: int) -> int:
    return max(1, min(minutes, 10080))


def limit(value: int) -> int:
    return max(1, min(value, 500))


async def stats(container: Container, caller: Caller,
                gateway_id: str | None, minutes: int) -> TrafficStats:
    return await container.read_models.stats(
        gateway_id, window(minutes), tenant_id=caller.tenant
    )


async def requests(container: Container, caller: Caller,
                   gateway_id: str | None, limit_: int) -> list[dict[str, Any]]:
    return await container.read_models.request_tail(
        gateway_id, limit(limit_), tenant_id=caller.tenant
    )


async def owned_aggregates(container: Container, caller: Caller) -> list[str]:
    """Every aggregate id this tenant owns: what the audit filter accepts."""
    return (
        [caller.tenant]
        + [
            row["id"]
            for row in await container.read_models.list_gateways(caller.tenant)
        ]
        + [
            row["operator_id"]
            for row in await container.read_models.list_operators(caller.tenant)
        ]
    )


async def events(container: Container, caller: Caller,
                 limit_: int) -> list[dict[str, Any]]:
    return await container.store.recent_events(
        limit=limit(limit_), aggregate_ids=await owned_aggregates(container, caller)
    )


__all__ = ["events", "limit", "owned_aggregates", "requests", "stats", "window"]
