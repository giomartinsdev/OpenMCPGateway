"""Request dependencies: the container, the session, and the permission gate.

The interface layer's job is to turn HTTP into a call. Everything that decides
*what* the call may do lives here so a route reads as `require(x)` plus one
command, and nothing else.

Why the gate is a dependency and not a decorator: FastAPI runs it after parsing
and before the body of the route, so an unauthorised request never reaches a
command — the refusal cannot be forgotten in a branch.
"""

from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.domain.permissions import (
    DEFAULT_ROLE_IDS,
    OWNER_ROLE,
    catalog_with_builtins,
    expand,
)
from gateway.interface.sessions import (
    SESSION_COOKIE,
    Session,
    SessionError,
    read_session,
)


def get_container(request: Request) -> Container:
    container = request.app.state.container
    if container is None:
        raise HTTPException(status_code=503, detail="starting up")
    return container


def current_session(request: Request, container: ContainerDep) -> Session:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        return read_session(
            token, secret=container.settings.session_secret.get_secret_value()
        )
    except SessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


ContainerDep = Annotated[Container, Depends(get_container)]
SessionDep = Annotated[Session, Depends(current_session)]


class Principal:
    """Who is asking, which tenant the question is about, and what they may do.

    Permissions are resolved per request from the read models rather than carried
    in the cookie: editing a role must take effect on the next click, not on the
    next sign-in. `tenant_id` is read the same way, because belonging changes
    after the cookie was issued. Two small reads per admin request is the price,
    and the panel is not a hot path.
    """

    __slots__ = ("permissions", "session", "tenant_id")

    def __init__(self, session: Session, tenant_id: str | None = None,
                 permissions: Iterable[str] = ()) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.permissions = frozenset(permissions)

    @property
    def onboarded(self) -> bool:
        return self.tenant_id is not None

    @property
    def tenant(self) -> str:
        """The tenant a scoped call belongs to. Guaranteed for gated routes."""
        if self.tenant_id is None:
            raise HTTPException(
                status_code=403, detail="onboarding: create or join a tenant first"
            )
        return self.tenant_id

    @property
    def caller(self) -> Caller:
        """The same identity, without the cookie and without `HTTPException`."""
        return Caller(
            operator_id=self.session.operator_id,
            email=self.session.email,
            is_admin=self.session.is_admin,
            tenant_id=self.tenant_id,
            permissions=self.permissions,
        )


async def current_principal(session: SessionDep,
                            container: ContainerDep) -> Principal:
    tenant_id = await container.read_models.operator_tenant(session.operator_id)
    role_ids = await container.read_models.operator_roles(session.operator_id)
    if role_ids is None:
        # An account with no projected row (written before roles existed): the
        # session's own flag is what it was granted with.
        role_ids = (OWNER_ROLE,) if session.is_admin else DEFAULT_ROLE_IDS
    if tenant_id is None:
        # No tenant, no permissions. An account with roles but nowhere to use
        # them would otherwise pass every gate and then query an unscoped read.
        return Principal(session=session, tenant_id=None)
    catalog = catalog_with_builtins(
        await container.read_models.role_permissions(tenant_id)
    )
    return Principal(
        session=session,
        tenant_id=tenant_id,
        permissions=expand(role_ids, catalog),
    )


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require(permission: str) -> Any:
    """A dependency that demands one permission and returns the session.

    A factory rather than one function per permission: the list of permissions is
    a data table (`domain.permissions`), and a function per entry would be a
    second copy of it that can drift.
    """

    async def guard(principal: PrincipalDep) -> Session:
        permits(principal, permission)
        return principal.session

    return guard


def permits(principal: Principal, permission: str) -> None:
    """The same rule as `require`, for routes that already hold the principal."""
    if not principal.onboarded:
        # A refused call should not send a brand-new account hunting for a role:
        # it has no tenant yet, and that is the next step.
        raise HTTPException(
            status_code=403, detail="onboarding: create or join a tenant first"
        )
    if permission not in principal.permissions:
        raise HTTPException(
            status_code=403, detail=f"this action requires '{permission}'"
        )


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
CanReadRoles = _can("roles:read")


__all__ = [
    "CanReadAudit",
    "CanReadConfig",
    "CanReadGateways",
    "CanReadKeys",
    "CanReadMembers",
    "CanReadRoles",
    "CanReadTraffic",
    "CanWriteConfig",
    "CanWriteGateways",
    "CanWriteKeys",
    "ContainerDep",
    "Principal",
    "PrincipalDep",
    "SessionDep",
    "current_principal",
    "current_session",
    "get_container",
    "permits",
    "require",
]
