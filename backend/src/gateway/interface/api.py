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
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from gateway.application.container import Container
from gateway.configfile import ConfigFileError, apply_document, parse_document
from gateway.domain.events import TENANT_OF_DEPLOYMENT, AuthMode, IdentityProvider
from gateway.domain.model import DomainError, SignInSettings
from gateway.domain.permissions import (
    BUILTIN_ROLES,
    DEFAULT_ROLE_IDS,
    OWNER_ROLE,
    PERMISSIONS,
    PermissionCatalogError,
    catalog_with_builtins,
    expand,
)
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


class Principal(BaseModel):
    """Who is asking, which tenant the question is about, and what they may do.

    Permissions are resolved per request from the read models rather than carried
    in the cookie: editing a role must take effect on the next click, not on the
    next sign-in. `tenant_id` is read the same way, because belonging changes
    after the cookie was issued. Two small reads per admin request is the price,
    and the panel is not a hot path.
    """

    session: Session
    tenant_id: str | None = None
    permissions: frozenset[str] = frozenset()

    @property
    def onboarded(self) -> bool:
        return self.tenant_id is not None


async def current_principal(request: Request, session: SessionDep) -> Principal:
    container: Container = request.app.state.container
    if container is None:
        raise HTTPException(status_code=503, detail="starting up")
    tenant_id = await container.read_models.operator_tenant(session.operator_id)
    role_ids = await container.read_models.operator_roles(session.operator_id)
    if role_ids is None:
        # An account with no projected row (written before roles existed): the
        # session's own flag is what it was granted with.
        role_ids = (OWNER_ROLE,) if session.is_admin else DEFAULT_ROLE_IDS
    if tenant_id is None:
        # No tenant, no permissions. An account with roles but nowhere to use
        # them would otherwise pass every `require(...)` gate and then query an
        # unscoped read model.
        return Principal(session=session, tenant_id=None)
    catalog = catalog_with_builtins(
        await container.read_models.role_permissions(tenant_id)
    )
    return Principal(
        session=session,
        tenant_id=tenant_id,
        permissions=frozenset(expand(role_ids, catalog)),
    )


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require(permission: str) -> Any:
    """A dependency that demands one permission and returns the session.

    A factory rather than one function per permission: the list of permissions is
    a data table (`domain.permissions`), and a function per entry would be a
    second copy of it that can drift.
    """

    async def guard(principal: PrincipalDep) -> Session:
        if not principal.onboarded:
            # A refused gateway call should not send a brand-new account hunting
            # for a role: it has no tenant yet, and that is the next step.
            raise HTTPException(
                status_code=403, detail="onboarding: create or join a tenant first"
            )
        if permission not in principal.permissions:
            raise HTTPException(
                status_code=403, detail=f"this action requires '{permission}'"
            )
        return principal.session

    return guard


def _tenant_of(principal: Principal) -> str:
    """The tenant a write belongs to. Every mutating route needs one."""
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=403, detail="onboarding: create or join a tenant first"
        )
    return principal.tenant_id

async def _gateway_or_404(container: Container, gateway_id: str,
                          principal: Principal) -> None:
    """Prove the gateway is this tenant's before touching anything under it.

    The id alone is not a capability: every `/gateways/{id}/...` route resolves
    the gateway through the tenant filter first, so a guessed id from another
    tenant is indistinguishable from one that does not exist.
    """
    gateway = await container.read_models.get_gateway(
        gateway_id, _tenant_of(principal)
    )
    if gateway is None:
        raise HTTPException(status_code=404, detail="gateway not found")


def _permits(principal: Principal, permission: str) -> None:
    if not principal.onboarded:
        raise HTTPException(
            status_code=403, detail="onboarding: create or join a tenant first"
        )
    if permission not in principal.permissions:
        raise HTTPException(
            status_code=403, detail=f"this action requires '{permission}'"
        )


def _grantable(principal: Principal, permissions: Iterable[str],
               default: Iterable[str] = ()) -> None:
    """Refuse to hand out a permission the caller does not hold itself.

    Without this, `members:write` is a path to `settings:write`: add an account,
    log in as it, done. Delegation may only narrow — an administrator who can do
    everything can grant anything, and everyone else grants a subset of their own.

    `default` is exempt: the default role is the floor a new account lands on,
    not a choice the caller made. A recruiter who may add people but not read
    gateways still must be able to create an account, exactly as registration
    would have.
    """
    beyond = sorted(set(permissions) - principal.permissions - set(default))
    if beyond:
        raise HTTPException(
            status_code=422,
            detail=(
                f"you cannot grant permission(s) you do not hold: {', '.join(beyond)}"
            ),
        )


def _permissions_of(
    role_ids: Iterable[str], catalog: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    return expand(role_ids, catalog)


def _can(permission: str) -> Any:
    """`Annotated[Session, Depends(...)]` under a name, for readable signatures."""
    return Annotated[Session, Depends(require(permission))]


CanReadGateways = _can("gateways:read")
CanWriteGateways = _can("gateways:write")
CanReadKeys = _can("keys:read")
CanWriteKeys = _can("keys:write")
CanReadTraffic = _can("traffic:read")
CanReadAudit = _can("audit:read")
CanReadConfig = _can("config:read")
CanWriteConfig = _can("config:write")
CanReadMembers = _can("members:read")
CanWriteMembers = _can("members:write")
CanReadRoles = _can("roles:read")
CanWriteRoles = _can("roles:write")
CanReadSettings = _can("settings:read")
CanWriteSettings = _can("settings:write")


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionCatalogError):
        # A permission outside the catalog is a validation failure, like any
        # other bad field, and must say which field.
        return HTTPException(status_code=422, detail=str(exc))
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

class OperatorCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=12)
    # Empty means the default role: an omission must never widen access.
    role_ids: list[str] = Field(default_factory=list)

class RoleAssignRequest(BaseModel):
    role_ids: list[str] = Field(default_factory=list)

class RoleRequest(BaseModel):
    name: str
    permissions: list[str] = Field(default_factory=list)


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
    return {
        "email": operator.email,
        "is_admin": operator.is_admin,
        "tenant_id": operator.tenant_id,
    }


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "logged out"}


@router.get("/auth/me")
async def me(principal: PrincipalDep,
             container: ContainerDep) -> dict[str, Any]:
    """Identity, tenant and permissions in one read — what the panel renders from."""
    session = principal.session
    role_ids = await container.read_models.operator_roles(session.operator_id)
    if role_ids is None:
        role_ids = (OWNER_ROLE,) if session.is_admin else DEFAULT_ROLE_IDS
    tenant = (
        await container.read_models.tenant(principal.tenant_id)
        if principal.tenant_id
        else None
    )
    roles = (
        {role["id"]: role["name"] for role in await _all_roles(container,
                                                               principal.tenant_id)}
        if principal.tenant_id
        else {}
    )
    return {
        "email": session.email,
        "operator_id": session.operator_id,
        "tenant_id": principal.tenant_id,
        "tenant_name": tenant.name if tenant else None,
        "role": session.role,
        "role_ids": list(role_ids),
        "roles": [roles.get(role_id, role_id) for role_id in role_ids],
        "permissions": sorted(principal.permissions),
        # What the login flow renders instead of a page it cannot fill. A panel
        # with no tenant has nothing to show, so the choice *is* the page.
        "onboarding": ["create", "join"] if not principal.onboarded else [],
        # Kept for the pages written before permissions, and it means exactly
        # "owner" of the current tenant.
        "is_admin": OWNER_ROLE in role_ids and principal.onboarded,
    }

# --------------------------------------------------------------------------
# Tenants — creating one, and joining one with a code
# --------------------------------------------------------------------------

class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)

class TenantJoinRequest(BaseModel):
    code: str = Field(min_length=1)

@router.post("/tenants", status_code=201)
async def create_tenant(payload: TenantCreateRequest,
                        principal: PrincipalDep,
                        container: ContainerDep) -> dict[str, Any]:
    """Create a tenant and own it.

    The roles come from the command, not from the caller: the account that
    creates a tenant is its owner by construction, so there is nothing to
    validate against its current permissions (it has none yet).
    """
    try:
        return await container.commands.create_tenant(
            name=payload.name, owner_id=principal.session.operator_id,
            actor=principal.session.email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc

@router.post("/tenants/join")
async def join_tenant(payload: TenantJoinRequest,
                      principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, Any]:
    """Join with the code the tenant's owner shared."""
    try:
        result = await container.commands.join_tenant(
            join_code=payload.code,
            operator_id=principal.session.operator_id,
            actor=principal.session.email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return result

@router.get("/tenants/current")
async def current_tenant(principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, Any]:
    """The tenant this account belongs to, including the code to share.

    Readable by every member: the code is what a colleague needs to join, and
    hiding it from everyone but the owner is what makes "ask someone to add you"
    the only path in.
    """
    tenant_id = _tenant_of(principal)
    tenant = await container.read_models.tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {
        "tenant_id": tenant["id"],
        "name": tenant["name"],
        "join_code": tenant["join_code"],
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

    operator_id = await container.store.fetch_operator_by_email(identity.email)
    if operator_id is None:
        # Anyone the provider vouches for gets an identity. Without a tenant it
        # can read nothing until it creates one or joins with a code, which is
        # what makes an open sign-up safe. The allowlist is the shortcut for
        # people this deployment already knows: they skip the choice and land in
        # the installation's own tenant.
        allowlist = (await _sign_in_settings(container)).operator_allowlist
        known = identity.email.strip().lower() in {
            email.lower() for email in allowlist
        }
        if known:
            await container.commands.ensure_tenant(
                tenant_id=TENANT_OF_DEPLOYMENT, name="Default"
            )
        try:
            operator_id = await container.commands.register_sso_operator(
                email=identity.email,
                subject=identity.subject,
                tenant_id=TENANT_OF_DEPLOYMENT if known else None,
                role_ids=DEFAULT_ROLE_IDS if known else None,
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
async def read_sign_in_settings(session: CanReadSettings,
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
async def write_sign_in_settings(payload: SignInSettingsPayload, session: CanWriteSettings,
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
        # Whether a deployment already knows some addresses. The login page uses
        # it to explain the difference; it is not a gate.
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
async def register(payload: RegisterRequest, response: Response,
                   container: ContainerDep) -> dict[str, Any]:
    """Create an account. Open to anyone — the account starts with no tenant.

    Registration used to be the installation's front door, so it had to stay
    shut: whoever signed up administered every gateway on the deployment. Now
    signing up grants nothing but an identity — a tenant, and the access that
    comes with it, is a separate choice (`POST /api/tenants`, or joining with a
    code). An empty account is a safe thing to hand out.
    """
    try:
        operator_id = await container.commands.register_operator(
            email=payload.email,
            password=payload.password,
            tenant_id=None,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    # Sign them in straight away: the next screen is the tenant choice, and
    # making them type the password again to reach it is the friction this
    # change removes.
    token = issue_session(
        secret=container.settings.session_secret.get_secret_value(),
        operator_id=operator_id,
        email=payload.email,
        is_admin=False,
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
    return {"operator_id": operator_id, "tenant_id": None, "is_admin": False}


@router.post("/operators", status_code=201)
async def add_operator(payload: OperatorCreateRequest, principal: PrincipalDep,
                       container: ContainerDep) -> dict[str, Any]:
    """Add an account to the tenant with an explicit set of roles.

    The alternative — leaving registration open — means anyone who finds the URL
    joins the tenant that administers every gateway. No roles means the default
    (viewer), so an omission narrows access instead of widening it.
    """
    _permits(principal, "members:write")
    try:
        role_ids = await _resolve_roles(
            container, _tenant_of(principal), payload.role_ids
        )
        catalog = await _catalog(container, _tenant_of(principal))
        _grantable(
            principal,
            _permissions_of(role_ids, catalog),
            _permissions_of(DEFAULT_ROLE_IDS, catalog),
        )
        operator_id = await container.commands.register_operator(
            email=payload.email,
            password=payload.password,
            is_admin=False,
            role_ids=role_ids,
            tenant_id=_tenant_of(principal),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "operator_id": operator_id,
        "tenant_id": _tenant_of(principal),
        "is_admin": OWNER_ROLE in role_ids,
        "role_ids": list(role_ids),
    }

@router.get("/operators")
async def list_operators(session: CanReadMembers,
                         container: ContainerDep) -> dict[str, Any]:
    """Who belongs to the tenant, with the role names resolved for display."""
    tenant_id = await container.read_models.operator_tenant(session.operator_id)
    roles = {
        role["id"]: role["name"] for role in await _all_roles(container, tenant_id)
    }
    return {
        "operators": [
            {**operator, "roles": [roles.get(r, r) for r in operator["role_ids"]]}
            for operator in await container.read_models.list_operators(tenant_id)
        ]
    }

@router.put("/operators/{operator_id}/roles")
async def assign_roles(operator_id: str, payload: RoleAssignRequest,
                       principal: PrincipalDep,
                       container: ContainerDep) -> dict[str, Any]:
    """Replace an account's roles. Takes effect on their next request: the
    session carries role ids, and permissions are resolved per request."""
    _permits(principal, "roles:write")
    session = principal.session
    try:
        role_ids = await _resolve_roles(
            container, _tenant_of(principal), payload.role_ids
        )
        _grantable(
            principal,
            _permissions_of(role_ids, await _catalog(container, _tenant_of(principal))),
        )
        await container.commands.assign_roles(
            operator_id, role_ids=role_ids, actor=session.email
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {"status": "assigned", "role_ids": list(role_ids)}

@router.get("/roles")
async def list_roles(session: CanReadRoles,
                     container: ContainerDep) -> dict[str, Any]:
    return {
        "roles": await _all_roles(
            container, await container.read_models.operator_tenant(session.operator_id)
        ),
        "permissions": list(PERMISSIONS),
    }

@router.post("/roles", status_code=201)
async def define_role(payload: RoleRequest, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    _permits(principal, "roles:write")
    session = principal.session
    try:
        _grantable(principal, payload.permissions)
        role_id = await container.commands.define_role(
            tenant_id=_tenant_of(principal),
            name=payload.name,
            permissions=payload.permissions,
            actor=session.email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {"id": role_id}

@router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: RoleRequest, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    _permits(principal, "roles:write")
    session = principal.session
    if role_id in BUILTIN_ROLES:
        raise HTTPException(
            status_code=422, detail="built-in roles cannot be edited"
        )
    try:
        _grantable(principal, payload.permissions)
        await container.commands.define_role(
            tenant_id=_tenant_of(principal),
            role_id=role_id,
            name=payload.name,
            permissions=payload.permissions,
            actor=session.email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {"status": "updated"}

@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, principal: PrincipalDep,
                      container: ContainerDep) -> dict[str, str]:
    _permits(principal, "roles:write")
    session = principal.session
    if role_id in BUILTIN_ROLES:
        raise HTTPException(status_code=422, detail="built-in roles cannot be deleted")
    holders = await container.read_models.role_holder_count(
        role_id, _tenant_of(principal)
    )
    if holders:
        raise HTTPException(
            status_code=422,
            detail=f"role is assigned to {holders} member(s); reassign them first",
        )
    try:
        await container.commands.delete_role(role_id, actor=session.email)
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {"status": "deleted"}


async def _all_roles(container: Container,
                     tenant_id: str | None) -> list[dict[str, Any]]:
    """Built-ins first, then the tenant's own, with their permissions expanded.

    Holder counts come along because the panel shows them next to the delete
    button, and "why can I not delete this" answered in the same table beats a
    second round trip that fails with a 422.
    """
    holder_counts = (
        await container.read_models.role_holder_counts(tenant_id)
        if tenant_id
        else {}
    )
    roles = [
        {
            "id": role_id,
            "name": name,
            "permissions": list(perms),
            "builtin": True,
            "holders": holder_counts.get(role_id, 0),
        }
        for role_id, (name, perms) in BUILTIN_ROLES.items()
    ]
    roles += [
        {
            "id": role["id"],
            "name": role["name"],
            "permissions": list(role["permissions"]),
            "builtin": False,
            "holders": holder_counts.get(role["id"], 0),
        }
        for role in await container.read_models.list_roles(tenant_id)
    ]
    return roles


async def _catalog(container: Container,
                   tenant_id: str | None) -> dict[str, tuple[str, ...]]:
    """Role id -> permissions, built-ins included. What `expand` needs."""
    return catalog_with_builtins(
        await container.read_models.role_permissions(tenant_id)
        if tenant_id
        else {}
    )


async def _resolve_roles(container: Container, tenant_id: str | None,
                         role_ids: list[str]) -> tuple[str, ...]:
    """Validate role ids against what exists, defaulting to the viewer role.

    Unknown ids are refused rather than dropped: silently granting less than the
    administrator asked for is the failure that gets discovered as "the new hire
    cannot do their job", days later.
    """
    if not role_ids:
        return DEFAULT_ROLE_IDS
    known = {
        role["id"] for role in await _all_roles(container, tenant_id)
    }
    unknown = sorted(set(role_ids) - known)
    if unknown:
        raise DomainError(
            f"unknown role(s): {', '.join(unknown)} — create them under Roles first"
        )
    return tuple(dict.fromkeys(role_ids))

# --------------------------------------------------------------------------
# Gateways
# --------------------------------------------------------------------------


@router.get("/gateways")
async def list_gateways(session: CanReadGateways, container: ContainerDep) -> dict[str, Any]:
    tenant_id = await container.read_models.operator_tenant(session.operator_id)
    gateways = await container.read_models.list_gateways(tenant_id)
    return {"gateways": [_public_gateway(container, gw) for gw in gateways]}


@router.post("/gateways", status_code=201)
async def create_gateway(payload: GatewayCreateRequest, session: CanWriteGateways,
                         container: ContainerDep) -> dict[str, str]:
    try:
        gateway_id = await container.commands.create_gateway(
            tenant_id=await container.read_models.operator_tenant(session.operator_id),
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
async def get_gateway(gateway_id: str, session: CanReadGateways,
                      container: ContainerDep) -> dict[str, Any]:
    gateway = await container.read_models.get_gateway(
        gateway_id, await container.read_models.operator_tenant(session.operator_id)
    )
    if gateway is None:
        raise HTTPException(status_code=404, detail="gateway not found")
    detail = _public_gateway(container, gateway)
    detail["pending"] = container.router.pending.get(gateway["slug"])
    detail["principals"] = await container.read_models.principals(gateway_id)
    detail["api_keys"] = await container.read_models.api_keys(gateway_id)
    detail["requests"] = await container.read_models.request_tail(
        gateway_id, 50, tenant_id=gateway["tenant_id"]
    )
    detail["stats"] = await container.read_models.stats(
        gateway_id, 60, tenant_id=gateway["tenant_id"]
    )
    return detail


@router.patch("/gateways/{gateway_id}")
async def update_gateway(gateway_id: str, payload: GatewayUpdateRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
async def delete_gateway(gateway_id: str, session: CanWriteGateways,
                         principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
    try:
        await container.commands.delete_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "deleted"}


@router.put("/gateways/{gateway_id}/auth")
async def configure_auth(gateway_id: str, payload: AuthConfigRequest,
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
                         session: CanWriteGateways, principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
async def disable_gateway(gateway_id: str, session: CanWriteGateways,
                          principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
    try:
        await container.commands.disable_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "disabled"}


@router.post("/gateways/{gateway_id}/enable")
async def enable_gateway(gateway_id: str, session: CanWriteGateways,
                         principal: PrincipalDep,
                         container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
    try:
        await container.commands.enable_gateway(gateway_id, actor=session.email)
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return {"status": "enabled"}


# -- principals ------------------------------------------------------------


@router.get("/gateways/{gateway_id}/principals")
async def list_principals(gateway_id: str, session: CanReadGateways,
                          principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, Any]:
    del session
    await _gateway_or_404(container, gateway_id, principal)
    return {"principals": await container.read_models.principals(gateway_id)}


@router.post("/gateways/{gateway_id}/principals", status_code=201)
async def allow_principal(gateway_id: str, payload: PrincipalRequest,
                          session: CanWriteGateways, principal: PrincipalDep,
                          container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
async def revoke_principal(gateway_id: str, subject: str, session: CanWriteGateways,
                           principal: PrincipalDep,
                           container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
async def list_keys(gateway_id: str, session: CanReadKeys,
                    principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, Any]:
    del session
    await _gateway_or_404(container, gateway_id, principal)
    return {"keys": await container.read_models.api_keys(gateway_id)}


@router.post("/gateways/{gateway_id}/keys", status_code=201)
async def issue_key(gateway_id: str, payload: ApiKeyRequest, session: CanWriteKeys,
                    principal: PrincipalDep,
                    container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
    try:
        plaintext, key_id = await container.commands.issue_api_key(
            gateway_id, label=payload.label, actor=session.email
        )
    except Exception as exc:
        raise _translate(exc) from exc
    # Shown once. Not stored, not recoverable.
    return {"key_id": key_id, "api_key": plaintext}


@router.delete("/gateways/{gateway_id}/keys/{key_id}")
async def revoke_key(gateway_id: str, key_id: str, session: CanWriteKeys,
                     principal: PrincipalDep,
                     container: ContainerDep) -> dict[str, str]:
    await _gateway_or_404(container, gateway_id, principal)
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
async def stats(session: CanReadTraffic, container: ContainerDep,
                gateway_id: str | None = None, minutes: int = 60) -> TrafficStats:
    del session
    return await container.read_models.stats(gateway_id, max(1, min(minutes, 10080)))


@router.get("/requests")
async def requests(session: CanReadTraffic, container: ContainerDep,
                   gateway_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    del session
    rows = await container.read_models.request_tail(
        gateway_id, max(1, min(limit, 500))
    )
    return {"requests": rows}


@router.get("/events")
async def events(session: CanReadAudit, container: ContainerDep,
                 limit: int = 100) -> dict[str, Any]:
    """Raw event log — the audit trail, with secrets redacted.

    Scoped to this tenant's streams: the log is shared by every tenant, and an
    audit page that lists all of them is a data leak with a search box.
    """
    tenant_id = await container.read_models.operator_tenant(session.operator_id)
    if tenant_id is None:
        raise HTTPException(
            status_code=403, detail="onboarding: create or join a tenant first"
        )
    owned = [tenant_id] + [
        row["id"]
        for row in await container.read_models.list_gateways(tenant_id)
    ] + [
        row["operator_id"]
        for row in await container.read_models.list_operators(tenant_id)
    ]
    return {
        "events": await container.store.recent_events(
            limit=max(1, min(limit, 500)), aggregate_ids=owned
        )
    }


# --------------------------------------------------------------------------
# Declarative config
# --------------------------------------------------------------------------


@router.post("/config/validate")
async def validate_config(payload: ConfigFileRequest,
                          session: CanReadConfig) -> dict[str, Any]:
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
async def apply_config(payload: ConfigFileRequest, session: CanWriteConfig,
                       container: ContainerDep) -> dict[str, Any]:
    text = payload.yaml or json.dumps(payload.document or {})
    try:
        document = parse_document(text)
        result = await apply_document(
            document,
            container.commands,
            container.read_models,
            tenant_id=await container.read_models.operator_tenant(session.operator_id)
            or "",
            actor=session.email,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    await container.refresh_router()
    return result


@router.get("/config/export")
async def export_config(session: CanReadConfig,
                        container: ContainerDep) -> dict[str, Any]:
    """Render current state as YAML — round-trips through /config/apply.

    Secrets are deliberately omitted: the export is safe to commit, and an
    operator re-supplies client secrets via ${ENV} placeholders.
    """
    gateways = await container.read_models.list_gateways(
        await container.read_models.operator_tenant(session.operator_id)
    )
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
