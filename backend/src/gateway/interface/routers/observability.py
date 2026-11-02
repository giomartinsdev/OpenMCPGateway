"""Traffic and the audit trail."""

from typing import Any

from fastapi import APIRouter

from gateway.application.services import observability
from gateway.domain.readmodels import TrafficStats
from gateway.interface.dependencies import (
    CanReadAudit,
    CanReadTraffic,
    ContainerDep,
    PrincipalDep,
)

router = APIRouter()


@router.get("/stats")
async def stats(session: CanReadTraffic, principal: PrincipalDep,
                container: ContainerDep, gateway_id: str | None = None,
                minutes: int = 60) -> TrafficStats:
    del session
    return await observability.stats(container, principal.caller, gateway_id, minutes)


@router.get("/requests")
async def requests(session: CanReadTraffic, principal: PrincipalDep,
                   container: ContainerDep, gateway_id: str | None = None,
                   limit: int = 100) -> dict[str, Any]:
    del session
    return {
        "requests": await observability.requests(
            container, principal.caller, gateway_id, limit
        )
    }


@router.get("/events")
async def events(session: CanReadAudit, principal: PrincipalDep,
                 container: ContainerDep, limit: int = 100) -> dict[str, Any]:
    """Raw event log — the audit trail, with secrets redacted."""
    del session
    return {
        "events": await observability.events(container, principal.caller, limit)
    }


__all__ = ["router"]
