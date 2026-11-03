"""Listing: one place that decides what a page is and what it may be filtered by.

Every list endpoint had its own `limit` and no way to narrow anything, which made
the panel's search boxes a client-side `filter()` over the first hundred rows —
wrong as soon as there are a hundred and one. One function per list, all shaped
`(caller, window, filters) -> Page`, keeps "which rows" and "which page" from
being decided in the router.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.application.services import gateways as gateway_service
from gateway.application.services.observability import owned_aggregates
from gateway.application.services.roles import RoleCatalog
from gateway.domain.filters import (
    EVENT_FILTERS,
    GATEWAY_FILTERS,
    MEMBER_FILTERS,
    REQUEST_FILTERS,
    ROLE_FILTERS,
    Clause,
    parse,
)
from gateway.domain.paging import Page, Window


async def gateways(
    container: Container, caller: Caller, *, window: Window,
    filters: dict[str, Any],
) -> Page:
    rows, next_cursor = await container.read_models.page_gateways(
        caller.tenant, window=window, clauses=_clauses(GATEWAY_FILTERS, filters)
    )
    return Page(
        items=[gateway_service.public(container, row) for row in rows],
        next=next_cursor,
    )

async def requests(
    container: Container, caller: Caller, *, window: Window,
    filters: dict[str, Any],
) -> Page:
    """The traffic log, narrowed by any of its labelled columns."""
    rows, next_cursor = await container.read_models.page_requests(
        None, window, tenant_id=caller.tenant,
        clauses=_clauses(REQUEST_FILTERS, filters),
    )
    # The log stores `gateway_id`; every reader wants the slug it can recognise.
    names = await _gateway_names(container, caller)
    return Page(
        items=[_request(names, row) for row in rows], next=next_cursor
    )

async def events(
    container: Container, caller: Caller, *, window: Window,
    filters: dict[str, Any],
) -> Page:
    rows, next_cursor = await container.store.page_events(
        window,
        aggregate_ids=await owned_aggregates(container, caller),
        clauses=_clauses(EVENT_FILTERS, filters),
    )
    return Page(items=rows, next=next_cursor)

async def members(
    container: Container, caller: Caller, *, window: Window,
    filters: dict[str, Any],
) -> Page:
    """The address book, paged, with role names resolved for display."""
    rows, next_cursor = await container.read_models.page_operators(
        caller.tenant, window=window, clauses=_clauses(MEMBER_FILTERS, filters)
    )
    names = {
        role["id"]: role["name"]
        for role in await RoleCatalog(container).all(caller.tenant)
    }
    return Page(
        items=[
            {**row, "roles": [names.get(r, r) for r in row["role_ids"]]}
            for row in rows
        ],
        next=next_cursor,
    )


async def roles(
    container: Container, caller: Caller, *, window: Window,
    filters: dict[str, Any],
) -> Page:
    """The tenant's own roles, paged.

    Built-ins are not paged with them: they are two fixed rows that belong at the
    top of every page, and a cursor over a constant list is noise.
    """
    rows, next_cursor = await container.read_models.page_roles(
        caller.tenant, window=window, clauses=_clauses(ROLE_FILTERS, filters)
    )
    # Holder counts come along because the panel shows them next to the delete
    # button: "why can I not delete this" answered where the question is asked.
    holders = await container.read_models.role_holder_counts(caller.tenant)
    return Page(
        items=[
            {**row, "builtin": False, "holders": holders.get(row["id"], 0)}
            for row in rows
        ],
        next=next_cursor,
    )


def _clauses(catalog: dict[str, Any], filters: dict[str, Any]) -> list[Clause]:
    return parse(catalog, filters)

async def _gateway_names(
    container: Container, caller: Caller
) -> dict[str, str]:
    return {
        row["id"]: row["slug"]
        for row in await container.read_models.list_gateways(caller.tenant)
    }

def _request(names: dict[str, str], row: Any) -> dict[str, Any]:
    item = dict(row)
    item["gateway"] = names.get(item["gateway_id"], item["gateway_id"])
    return item

__all__ = ["events", "gateways", "members", "requests", "roles"]
