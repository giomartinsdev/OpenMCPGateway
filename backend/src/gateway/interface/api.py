"""Admin HTTP API — the only surface the React UI talks to.

Every endpoint here is a thin translation of a command or a read model query;
no business rules live in this file. That is what keeps the UI and a YAML file
genuinely equivalent: both end up in `CommandHandler`.

Responses never include secrets (password hashes, key hashes, upstream OAuth
client secrets). The one exception is an API key's plaintext, which is returned
exactly once at issue time and never stored.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from gateway.application.container import Container
from gateway.configfile import ConfigFileError, apply_document, parse_document
from gateway.domain.events import AuthMode, IdentityProvider
from gateway.domain.model import DomainError
from gateway.infrastructure.eventstore.postgres import ConcurrencyError
from gateway.interface.sessions import (
    SESSION_COOKIE,
    Session,
    SessionError,
    issue_session,
    read_session,
)

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def get_container(request: Request) -> Container:
    container = request.app.state.container
    if container is None:
        raise HTTPException(status_code=503, detail="starting up")
    return container


def current_session(request: Request) -> Session:
    container: Container = request.app.state.container
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        return read_session(token, secret=container.settings.session_secret)
    except SessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


SessionDep = Annotated[Session, Depends(current_session)]
ContainerDep = Annotated[Container, Depends(get_container)]


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, DomainError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ConcurrencyError):
        return HTTPException(
            status_code=409,
            detail=f"conflict, reload and retry: {exc}",
        )
    if isinstance(exc, ConfigFileError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=12)


class GatewayCreateRequest(BaseModel):
    slug: str
    name: str
    target_url: str
    description: str = ""
    auth_mode: AuthMode = AuthMode.NONE
    idp: IdentityProvider | None = None


class GatewayUpdateRequest(BaseModel):
    name: str | None = None
    target_url: str | None = None
    description: str | None = None


class AuthConfigRequest(BaseModel):
    auth_mode: AuthMode
    idp: IdentityProvider | None = None
    client_id: str | None = None
    client_secret: str | None = None
    config_url: str | None = None
    required_scopes: list[str] = Field(default_factory=list)
    # Bearer the upstream MCP server demands. Write-only: never read back.
    upstream_token: str | None = None


class RateLimitRequest(BaseModel):
    requests_per_minute: int = Field(ge=0)


class PrincipalRequest(BaseModel):
    subject: str
    note: str = ""


class ApiKeyRequest(BaseModel):
    label: str


class ConfigFileRequest(BaseModel):
    """Either raw YAML text or an already-parsed mapping."""

    yaml: str | None = None
    document: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


@router.post("/auth/login")
async def login(payload: LoginRequest, response: Response,
                container: ContainerDep) -> dict[str, Any]:
    operator = await container.commands.authenticate_operator(
        email=payload.email, password=payload.password
    )
    if operator is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = issue_session(
        secret=container.settings.session_secret,
        operator_id=operator.id,
        email=operator.email,
        is_admin=operator.is_admin,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=container.settings.base_url.startswith("https://"),
        max_age=60 * 60 * 12,
        path="/",
    )
    return {"email": operator.email, "is_admin": operator.is_admin}


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "logged out"}


@router.get("/auth/me")
async def me(session: SessionDep) -> dict[str, Any]:
    return {
        "email": session.email,
        "operator_id": session.operator_id,
        "is_admin": session.is_admin,
    }


@router.post("/auth/register", status_code=201)
async def register(payload: RegisterRequest, container: ContainerDep) -> dict[str, str]:
    try:
        operator_id = await container.commands.register_operator(
            email=payload.email, password=payload.password
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {"operator_id": operator_id}


# --------------------------------------------------------------------------
# Gateways
# --------------------------------------------------------------------------


@router.get("/gateways")
async def list_gateways(session: SessionDep, container: ContainerDep) -> dict[str, Any]:
    del session
    gateways = await container.read_models.list_gateways()
    return {"gateways": [_public_gateway(container, gw) for gw in gateways]}


@router.post("/gateways", status_code=201)
async def create_gateway(payload: GatewayCreateRequest, session: SessionDep,
                         container: ContainerDep) -> dict[str, str]:
    try:
        gateway_id = await container.commands.create_gateway(
            slug=payload.slug,
            name=payload.name,
            target_url=payload.target_url,
            description=payload.description,
            auth_mode=payload.auth_mode,
            idp=payload.idp,
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"id": gateway_id}


@router.get("/gateways/{gateway_id}")
async def get_gateway(gateway_id: str, session: SessionDep,
                      container: ContainerDep) -> dict[str, Any]:
    del session
    gateway = await container.read_models.get_gateway(gateway_id)
    if gateway is None:
        raise HTTPException(status_code=404, detail="gateway not found")
    detail = _public_gateway(container, gateway)
    detail["pending"] = container.router.pending.get(gateway["slug"])
    detail["principals"] = await container.read_models.principals(gateway_id)
    detail["api_keys"] = await container.read_models.api_keys(gateway_id)
    detail["requests"] = await container.read_models.request_tail(gateway_id, 50)
    detail["stats"] = await container.read_models.stats(gateway_id, 60)
    return detail


@router.patch("/gateways/{gateway_id}")
async def update_gateway(gateway_id: str, payload: GatewayUpdateRequest,
                         session: SessionDep, container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.update_gateway(
            gateway_id,
            name=payload.name,
            target_url=payload.target_url,
            description=payload.description,
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "updated"}


@router.delete("/gateways/{gateway_id}")
async def delete_gateway(gateway_id: str, session: SessionDep,
                         container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.delete_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "deleted"}


@router.put("/gateways/{gateway_id}/auth")
async def configure_auth(gateway_id: str, payload: AuthConfigRequest,
                         session: SessionDep, container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=payload.auth_mode,
            idp=payload.idp,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            config_url=payload.config_url,
            required_scopes=tuple(payload.required_scopes),
            upstream_token=payload.upstream_token,
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "configured"}


@router.put("/gateways/{gateway_id}/rate-limit")
async def set_rate_limit(gateway_id: str, payload: RateLimitRequest,
                         session: SessionDep, container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.set_rate_limit(
            gateway_id,
            requests_per_minute=payload.requests_per_minute,
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {"status": "configured"}


@router.post("/gateways/{gateway_id}/disable")
async def disable_gateway(gateway_id: str, session: SessionDep,
                          container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.disable_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "disabled"}


@router.post("/gateways/{gateway_id}/enable")
async def enable_gateway(gateway_id: str, session: SessionDep,
                         container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.enable_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "enabled"}


# -- principals ------------------------------------------------------------


@router.get("/gateways/{gateway_id}/principals")
async def list_principals(gateway_id: str, session: SessionDep,
                          container: ContainerDep) -> dict[str, Any]:
    del session
    return {"principals": await container.read_models.principals(gateway_id)}


@router.post("/gateways/{gateway_id}/principals", status_code=201)
async def allow_principal(gateway_id: str, payload: PrincipalRequest,
                          session: SessionDep, container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.allow_principal(
            gateway_id, subject=payload.subject, note=payload.note,
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "allowed"}


@router.delete("/gateways/{gateway_id}/principals/{subject}")
async def revoke_principal(gateway_id: str, subject: str, session: SessionDep,
                           container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.revoke_principal(
            gateway_id, subject=subject, actor=session.email
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "revoked"}


# -- api keys --------------------------------------------------------------


@router.get("/gateways/{gateway_id}/keys")
async def list_keys(gateway_id: str, session: SessionDep,
                    container: ContainerDep) -> dict[str, Any]:
    del session
    return {"keys": await container.read_models.api_keys(gateway_id)}


@router.post("/gateways/{gateway_id}/keys", status_code=201)
async def issue_key(gateway_id: str, payload: ApiKeyRequest, session: SessionDep,
                    container: ContainerDep) -> dict[str, str]:
    try:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway_id, label=payload.label, actor=session.email
        )
    except Exception as exc:
        raise _translate(exc) from exc
    # Shown once. Not stored, not recoverable.
    return {"key_id": key_id, "api_key": plaintext}


@router.delete("/gateways/{gateway_id}/keys/{key_id}")
async def revoke_key(gateway_id: str, key_id: str, session: SessionDep,
                     container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.revoke_api_key(
            gateway_id, key_id=key_id, actor=session.email
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "revoked"}


# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------


@router.get("/stats")
async def stats(session: SessionDep, container: ContainerDep,
                gateway_id: str | None = None, minutes: int = 60) -> dict[str, Any]:
    del session
    return await container.read_models.stats(gateway_id, max(1, min(minutes, 10080)))


@router.get("/requests")
async def requests(session: SessionDep, container: ContainerDep,
                   gateway_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    del session
    rows = await container.read_models.request_tail(
        gateway_id, max(1, min(limit, 500))
    )
    return {"requests": rows}


@router.get("/events")
async def events(session: SessionDep, container: ContainerDep,
                 limit: int = 100) -> dict[str, Any]:
    """Raw event log — the audit trail, with secrets redacted."""
    del session
    return {"events": await container.store.recent_events(limit=max(1, min(limit, 500)))}


# --------------------------------------------------------------------------
# Declarative config
# --------------------------------------------------------------------------


@router.post("/config/validate")
async def validate_config(payload: ConfigFileRequest,
                          session: SessionDep) -> dict[str, Any]:
    del session
    text = payload.yaml or json.dumps(payload.document or {})
    try:
        document = parse_document(text)
    except ConfigFileError as exc:
        raise _translate(exc) from exc
    return {
        "gateways": [spec.slug for spec in document.gateways],
        "prune": document.prune,
    }


@router.post("/config/apply")
async def apply_config(payload: ConfigFileRequest, session: SessionDep,
                       container: ContainerDep) -> dict[str, Any]:
    text = payload.yaml or json.dumps(payload.document or {})
    try:
        document = parse_document(text)
        result = await apply_document(
            document, container.commands, container.read_models, actor=session.email
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return result


@router.get("/config/export")
async def export_config(session: SessionDep,
                        container: ContainerDep) -> dict[str, Any]:
    """Render current state as YAML — round-trips through /config/apply.

    Secrets are deliberately omitted: the export is safe to commit, and an
    operator re-supplies client secrets via ${ENV} placeholders.
    """
    del session
    gateways = await container.read_models.list_gateways()
    entries: list[str] = []
    for gateway in gateways:
        principals = await container.read_models.principals(gateway["id"])
        keys = await container.read_models.api_keys(gateway["id"])
        entries.append(_gateway_yaml(gateway, principals, keys))
    body = "gateways:\n" + "\n".join(entries) if entries else "gateways: []\n"
    return {"yaml": body}


def _gateway_yaml(gateway: dict[str, Any], principals: list[dict[str, Any]],
                  keys: list[dict[str, Any]]) -> str:
    lines = [
        f"  - slug: {gateway['slug']}",
        f"    name: {gateway['name']!r}",
        f"    target_url: {gateway['target_url']}",
    ]
    if gateway.get("description"):
        lines.append(f"    description: {gateway['description']!r}")
    if gateway["auth_mode"] != "none":
        lines.append("    auth:")
        lines.append(f"      mode: {gateway['auth_mode']}")
        if gateway.get("idp"):
            lines.append(f"      idp: {gateway['idp']}")
        if gateway.get("client_id"):
            lines.append(f"      client_id: ${{CLIENT_ID_{gateway['slug'].upper()}}}")
        if gateway["auth_mode"] == "oauth":
            lines.append(
                f"      client_secret: ${{CLIENT_SECRET_{gateway['slug'].upper()}}}"
            )
            if gateway.get("config_url"):
                lines.append(f"      config_url: {gateway['config_url']}")
    if gateway.get("requests_per_minute"):
        lines.append(f"    rate_limit: {gateway['requests_per_minute']}")
    if principals:
        subjects = ", ".join(repr(p["subject"]) for p in principals)
        lines.append(f"    allow: [{subjects}]")
    if keys:
        labels = ", ".join(f"{k['label']!r}" for k in keys)
        lines.append(f"    api_keys: [{labels}]")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _public_gateway(container: Container, gateway: dict[str, Any]) -> dict[str, Any]:
    """Add derived fields; never leak secrets held in the write model."""
    result = {
        key: value
        for key, value in gateway.items()
        if key not in {"created_at", "updated_at"} or value is not None
    }
    result["created_at"] = gateway["created_at"].isoformat()
    result["updated_at"] = gateway["updated_at"].isoformat()
    result["endpoint"] = f"{container.settings.base_url}/g/{gateway['slug']}/mcp"
    return result


__all__ = ["router"]
