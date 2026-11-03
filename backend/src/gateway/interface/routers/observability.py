"""Traffic and the audit trail, both paged and both filterable.

The two lists an operator actually searches: "which calls failed for this
gateway", "when did someone change that role". The filter catalogue lives in
`gateway.domain.filters`; this module only names which catalogue a route uses.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from gateway.application.services import export, listing
from gateway.domain.filters import EVENT_FILTERS, REQUEST_FILTERS
from gateway.domain.readmodels import TrafficStats
from gateway.interface.dependencies import (
    CanReadAudit,
    CanReadTraffic,
    ContainerDep,
    PrincipalDep,
)
from gateway.interface.listing import PageQuery, filters

router = APIRouter()


@router.get("/stats")
async def stats(session: CanReadTraffic, principal: PrincipalDep,
                container: ContainerDep, gateway_id: str | None = None,
                minutes: int = 60) -> TrafficStats:
    del session
    from gateway.application.services import observability

    return await observability.stats(container, principal.caller, gateway_id, minutes)


@router.get("/requests")
async def requests(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadTraffic,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """A page of the request log. Any field of `REQUEST_FILTERS` narrows it."""
    del session
    result = await listing.requests(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, REQUEST_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    return {"requests": result.items, "next": result.next}


@router.get("/requests.csv")
async def requests_csv(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadTraffic,
    principal: PrincipalDep,
    container: ContainerDep,
) -> Response:
    """The same log as `/requests`, as one CSV file.

    Bounded by `limit` rather than streamed: an export that walks a whole table
    over one HTTP response is a query that outlives the request that started it.
    The caller pages with `before` like everywhere else; a SIEM that wants a
    window asks for a high limit.
    """
    del session
    result = await listing.requests(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, REQUEST_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    body = export.render(result.items)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="requests.csv"',
        },
    )


@router.get("/events")
async def events(
    request: Request,
    page: Annotated[PageQuery, Depends()],
    session: CanReadAudit,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """A page of the audit log. Any field of `EVENT_FILTERS` narrows it."""
    del session
    result = await listing.events(
        container,
        principal.caller,
        window=page.window,
        filters=filters(request, EVENT_FILTERS),
        sort=page.sort,
        order=page.order,
    )
    return {"events": result.items, "next": result.next}


__all__ = ["router"]
