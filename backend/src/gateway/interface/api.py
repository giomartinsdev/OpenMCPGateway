"""Admin HTTP API — the only surface the React UI talks to.

Every endpoint here is a thin translation of a command or a read model query;
no business rules live in this file. That is what keeps the UI and a YAML file
genuinely equivalent: both end up in `CommandHandler`.

Responses never include secrets (password hashes, key hashes, upstream OAuth
client secrets). The one exception is an API key's plaintext, which is returned
exactly once at issue time and never stored.
"""

import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from gateway.application.container import Container
from gateway.configfile import ConfigFileError, apply_document, parse_document
from gateway.domain.events import TENANT_OF_DEPLOYMENT, AuthMode, IdentityProvider
from gateway.domain.model import DomainError, SignInSettings
from gateway.domain.readmodels import TrafficStats
from gateway.infrastructure.eventstore.postgres import ConcurrencyError
from gateway.interface.google_login import (
    STATE_COOKIE,
    STATE_TTL_SECONDS,
    GoogleOIDC,
    LoginError,
    issue_state,
    read_state,
)
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
        return read_session(token, secret=container.settings.session_secret.get_secret_value())
    except SessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


SessionDep = Annotated[Session, Depends(current_session)]
ContainerDep = Annotated[Container, Depends(get_container)]


def admin_session(session: SessionDep) -> Session:
    """Settings that decide who can administer this installation.

    A plain operator must not be able to point sign-in at an IdP they control:
    that is a privilege escalation dressed as a configuration change.
    """
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="administrators only")
    return session


AdminDep = Annotated[Session, Depends(admin_session)]


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
        secret=container.settings.session_secret.get_secret_value(),
        operator_id=operator.id,
        email=operator.email,
        is_admin=operator.is_admin,
        tenant_id=operator.tenant_id,
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
        "tenant_id": session.tenant_id,
        "role": session.role,
        "is_admin": session.is_admin,
    }


@router.get("/auth/google/start")
async def google_start(container: ContainerDep) -> RedirectResponse:
    """Send the browser to Google, with a state bound to this browser.

    The state lives in a signed cookie so the callback can prove the browser
    asking is the browser that started. Without it a callback is unauthenticated
    input that ends in a session.
    """
    provider = await _google(container)
    state = issue_state(
        secret=container.settings.session_secret.get_secret_value(),
        redirect_uri=provider.redirect_uri,
    )
    url = await provider.authorization_url(state=state)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=container.settings.base_url.startswith("https://"),
        max_age=STATE_TTL_SECONDS,
        path="/api/auth/google",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(request: Request, container: ContainerDep,
                          code: str = "", state: str = "", error: str = "",
                          ) -> Response:
    """Verify what the provider vouched for, then start a session."""
    cookie = request.cookies.get(STATE_COOKIE) or ""
    # Compare before anything else: a mismatch means this callback did not come
    # from a browser we sent to the provider, so none of its parameters can be
    # trusted — including the code.
    if not cookie or not hmac.compare_digest(cookie, state):
        return _login_failed("login state does not match this browser")
    try:
        started = read_state(state, secret=container.settings.session_secret.get_secret_value())
    except LoginError as exc:
        return _login_failed(str(exc))
    if error:
        return _login_failed(f"provider reported: {error}")
    if not code:
        return _login_failed("provider returned no code")

    provider = await _google(container)
    # The redirect URI is part of what the provider matched; if the login started
    # under a different one, the code will not exchange and that is correct.
    del started
    try:
        identity = await provider.exchange(code=code)
    except Exception as exc:
        return _login_failed(str(exc))

    allowlist = (await _sign_in_settings(container)).operator_allowlist
    operator_id = await container.store.fetch_operator_by_email(identity.email)
    if operator_id is None:
        if identity.email not in allowlist:
            return _login_failed(
                f"'{identity.email}' is not an operator here and is not on "
                "OMG_OPERATOR_ALLOWLIST"
            )
        try:
            operator_id = await container.commands.register_sso_operator(
                email=identity.email, subject=identity.subject
            )
        except DomainError as exc:
            # Two tabs signing in at once: the other one created it. Reuse it.
            operator_id = await container.store.fetch_operator_by_email(
                identity.email
            )
            if operator_id is None:
                return _login_failed(str(exc))

    events = await container.store.load_stream(operator_id)
    from gateway.domain.model import Operator

    operator = Operator.from_events(operator_id, events)
    token = issue_session(
        secret=container.settings.session_secret.get_secret_value(),
        operator_id=operator_id,
        email=operator.email,
        is_admin=operator.is_admin,
        tenant_id=operator.tenant_id,
    )
    response = RedirectResponse("/#/gateways", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=container.settings.base_url.startswith("https://"),
        max_age=60 * 60 * 12,
        path="/",
    )
    response.delete_cookie(STATE_COOKIE, path="/api/auth/google")
    return response


class SignInSettingsPayload(BaseModel):
    google_client_id: str = ""
    # Write-only: absent means "keep the stored one".
    google_client_secret: str = ""
    redirect_uri: str = ""
    operator_allowlist: list[str] = Field(default_factory=list)


@router.get("/settings/sign-in")
async def read_sign_in_settings(session: AdminDep,
                               container: ContainerDep) -> dict[str, Any]:
    del session
    settings = await container.commands.read_sign_in_settings()
    return {
        "google_client_id": settings.google_client_id,
        "redirect_uri": settings.redirect_uri,
        "operator_allowlist": list(settings.operator_allowlist),
        "enabled": settings.enabled,
        # Presence only. A secret that round-trips to the browser ends up in a
        # screenshot, a HAR file and an issue report.
        "has_google_client_secret": bool(settings.google_client_secret),
    }


@router.put("/settings/sign-in")
async def write_sign_in_settings(payload: SignInSettingsPayload, session: AdminDep,
                                 container: ContainerDep) -> dict[str, Any]:
    try:
        await container.commands.configure_sign_in(
            google_client_id=payload.google_client_id,
            google_client_secret=payload.google_client_secret,
            redirect_uri=payload.redirect_uri,
            operator_allowlist=tuple(payload.operator_allowlist),
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    settings = await container.commands.read_sign_in_settings()
    return {"status": "configured", "enabled": settings.enabled}


@router.get("/auth/methods")
async def auth_methods(container: ContainerDep) -> dict[str, Any]:
    """What the login page may offer. A password form always exists."""
    settings = await container.commands.read_sign_in_settings()
    return {
        "password": True,
        "google": settings.enabled,
        "allowlist_configured": bool(settings.operator_allowlist),
    }


async def _sign_in_settings(container: Container) -> SignInSettings:
    """Panel settings, seeded from the environment on first boot.

    The environment is how a fresh deployment gets in; after that the panel owns
    the configuration, because the client id belongs to whoever runs it.
    """
    settings = await container.commands.read_sign_in_settings()
    if settings.version == 0:
        env = container.settings
        if env.google_login_enabled:
            return SignInSettings(
                google_client_id=env.google_client_id or "",
                google_client_secret=(
            env.google_client_secret.get_secret_value()
            if env.google_client_secret
            else ""
        ),
        redirect_uri=f"{env.base_url.rstrip('/')}/api/auth/google/callback",
                operator_allowlist=env.operator_allowlist,
            )
    return settings


async def _google(container: Container) -> GoogleOIDC:
    settings = await _sign_in_settings(container)
    if not settings.enabled:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured — set it under Settings in "
                   "the panel",
        )
    return GoogleOIDC(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        discovery_url=container.settings.google_discovery_url,
        redirect_uri=settings.redirect_uri,
    )


def _login_failed(reason: str) -> Response:
    """Back to the login page with the reason, not a dead end.

    A JSON error would leave the browser staring at an API document; the login
    page is where the person can act on it.
    """
    from urllib.parse import quote

    return RedirectResponse(f"/#/login?error={quote(reason)}", status_code=303)


@router.post("/auth/register", status_code=201)
async def register(payload: RegisterRequest, request: Request,
                   container: ContainerDep) -> dict[str, Any]:
    """Bootstrap the first operator; afterwards only an administrator may add one.

    Open registration on a self-hosted admin panel means anyone who finds the URL
    becomes an operator of your gateways. The only moment it must be open is a
    fresh install, and the account it creates is the administrator.
    """
    try:
        is_first = await container.store.count_operators() == 0
        if not is_first:
            token = request.cookies.get(SESSION_COOKIE) or ""
            try:
                caller = read_session(
                    token, secret=container.settings.session_secret.get_secret_value()
                )
            except SessionError as exc:
                raise HTTPException(
                    status_code=403,
                    detail="registration is closed — ask an administrator",
                ) from exc
            if not caller.is_admin:
                raise HTTPException(status_code=403, detail="administrators only")

        operator_id = await container.commands.register_operator(
            email=payload.email,
            password=payload.password,
            is_admin=is_first,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "operator_id": operator_id,
        "tenant_id": TENANT_OF_DEPLOYMENT,
        "is_admin": is_first,
    }


@router.post("/operators", status_code=201)
async def add_operator(payload: RegisterRequest, session: AdminDep,
                       container: ContainerDep) -> dict[str, Any]:
    """An administrator adds a member to their tenant.

    The alternative — leaving registration open — means anyone who finds the URL
    joins the tenant that administers every gateway. A member is read-only: the
    panel hides what they cannot do, and every mutating route checks the role
    again, because a hidden button is not an authorisation rule.
    """
    try:
        operator_id = await container.commands.register_operator(
            email=payload.email,
            password=payload.password,
            is_admin=False,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "operator_id": operator_id,
        "tenant_id": session.tenant_id,
        "is_admin": False,
    }


# --------------------------------------------------------------------------
# Gateways
# --------------------------------------------------------------------------

@router.get("/operators")
async def list_operators(session: AdminDep,
                         container: ContainerDep) -> dict[str, Any]:
    """Who belongs to the tenant. Administrator-only: it is an address book."""
    del session
    return {"operators": await container.store.list_operators()}


@router.get("/gateways")
async def list_gateways(session: SessionDep, container: ContainerDep) -> dict[str, Any]:
    del session
    gateways = await container.read_models.list_gateways()
    return {"gateways": [_public_gateway(container, gw) for gw in gateways]}


@router.post("/gateways", status_code=201)
async def create_gateway(payload: GatewayCreateRequest, session: AdminDep,
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
                         session: AdminDep, container: ContainerDep) -> dict[str, str]:
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
async def delete_gateway(gateway_id: str, session: AdminDep,
                         container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.delete_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "deleted"}


@router.put("/gateways/{gateway_id}/auth")
async def configure_auth(gateway_id: str, payload: AuthConfigRequest,
                         session: AdminDep, container: ContainerDep) -> dict[str, str]:
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
                         session: AdminDep, container: ContainerDep) -> dict[str, str]:
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
async def disable_gateway(gateway_id: str, session: AdminDep,
                          container: ContainerDep) -> dict[str, str]:
    try:
        await container.commands.disable_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "disabled"}


@router.post("/gateways/{gateway_id}/enable")
async def enable_gateway(gateway_id: str, session: AdminDep,
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
                          session: AdminDep, container: ContainerDep) -> dict[str, str]:
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
async def revoke_principal(gateway_id: str, subject: str, session: AdminDep,
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
async def issue_key(gateway_id: str, payload: ApiKeyRequest, session: AdminDep,
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
async def revoke_key(gateway_id: str, key_id: str, session: AdminDep,
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
                gateway_id: str | None = None, minutes: int = 60) -> TrafficStats:
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
async def apply_config(payload: ConfigFileRequest, session: AdminDep,
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
