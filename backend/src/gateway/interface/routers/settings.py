"""Sign-in settings: the panel half of configuring Google login."""

from typing import Any

from fastapi import APIRouter

from gateway.application.services import signin
from gateway.interface.dependencies import (
    CanReadSettings,
    CanWriteSettings,
    ContainerDep,
)
from gateway.interface.schemas import SignInSettingsPayload

router = APIRouter()


@router.get("/settings/sign-in")
async def read_sign_in_settings(session: CanReadSettings,
                               container: ContainerDep) -> dict[str, Any]:
    del session
    return signin.public(await signin.settings(container))


@router.put("/settings/sign-in")
async def write_sign_in_settings(payload: SignInSettingsPayload,
                                 session: CanWriteSettings,
                                 container: ContainerDep) -> dict[str, Any]:
    current = await signin.save(
        container,
        google_client_id=payload.google_client_id,
        google_client_secret=payload.google_client_secret,
        redirect_uri=payload.redirect_uri,
        operator_allowlist=payload.operator_allowlist,
        actor=session.email,
    )
    return {"status": "configured", "enabled": current.enabled}


__all__ = ["router"]
