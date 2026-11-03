"""Gateways as the panel sees them.

Three things that are not HTTP and used to live in the route: the derived
endpoint URL, the detail composition (principals, keys, recent requests, stats),
and the fact that a gateway id is only meaningful inside the caller's tenant.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.domain.errors import NotFound


def public(container: Container, gateway: dict[str, Any]) -> dict[str, Any]:
    """Add derived fields; never leak secrets held in the write model."""
    result = {
        key: value
        for key, value in gateway.items()
        if key not in {"created_at", "updated_at"} or value is not None
    }
    result["created_at"] = gateway["created_at"].isoformat()
    result["updated_at"] = gateway["updated_at"].isoformat()
    result["endpoint"] = endpoint_for(container, gateway["slug"])
    return result


def endpoint_for(container: Container, slug: str) -> str:
    return f"{container.settings.base_url}/g/{slug}/mcp"


async def list_(container: Container, caller: Caller) -> list[dict[str, Any]]:
    gateways = await container.read_models.list_gateways(caller.tenant)
    return [public(container, gateway) for gateway in gateways]


async def owned(container: Container, caller: Caller,
                gateway_id: str) -> dict[str, Any]:
    """The gateway, or `NotFound`. A guessed id from another tenant reads the same
    as an id that does not exist, which is the point."""
    gateway = await container.read_models.get_gateway(gateway_id, caller.tenant)
    if gateway is None:
        raise NotFound("gateway not found")
    return gateway


async def detail(container: Container, caller: Caller,
                 gateway_id: str) -> dict[str, Any]:
    gateway = await owned(container, caller, gateway_id)
    result = public(container, gateway)
    result["pending"] = container.router.pending.get(gateway["slug"])
    result["principals"] = await container.read_models.principals(gateway_id)
    result["api_keys"] = await container.read_models.api_keys(gateway_id)
    result["requests"] = await container.read_models.request_tail(
        gateway_id, 50, tenant_id=gateway["tenant_id"]
    )
    result["stats"] = await container.read_models.stats(
        gateway_id, 60, tenant_id=gateway["tenant_id"]
    )
    # `None` until something probed it: an empty badge is honest, and "up" that
    # was never checked is the kind of default that hides a dead upstream.
    health = container.prober.last(gateway_id)
    result["health"] = health.model_dump(mode="json") if health else None
    return result


__all__ = ["detail", "endpoint_for", "list_", "owned", "public"]
