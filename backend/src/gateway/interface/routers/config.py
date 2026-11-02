"""Declarative config: validate, apply, export."""

import json
from typing import Any

from fastapi import APIRouter

from gateway.configexport import render_document
from gateway.configfile import apply_document, parse_document
from gateway.interface.dependencies import (
    CanReadConfig,
    CanWriteConfig,
    ContainerDep,
    PrincipalDep,
)
from gateway.interface.schemas import ConfigFileRequest

router = APIRouter()


@router.post("/config/validate")
async def validate_config(payload: ConfigFileRequest, session: CanReadConfig,
                          principal: PrincipalDep) -> dict[str, Any]:
    del session, principal
    text = payload.yaml or json.dumps(payload.document or {})
    document = parse_document(text)
    return {
        "gateways": [spec.slug for spec in document.gateways],
        "prune": document.prune,
    }


@router.post("/config/apply")
async def apply_config(payload: ConfigFileRequest, session: CanWriteConfig,
                       principal: PrincipalDep,
                       container: ContainerDep) -> dict[str, Any]:
    text = payload.yaml or json.dumps(payload.document or {})
    document = parse_document(text)
    result = await apply_document(
        document,
        container.commands,
        container.read_models,
        tenant_id=principal.tenant,
        actor=session.email,
    )
    await container.refresh_router()
    return result


@router.get("/config/export")
async def export_config(session: CanReadConfig, principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    """Render current state as YAML — round-trips through /config/apply."""
    del session
    return {
        "yaml": await render_document(container.read_models, principal.tenant)
    }


__all__ = ["router"]
