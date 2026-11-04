"""Signing in, out, and up. The only routes that do not need a permission."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from gateway.application.services import account, signin
from gateway.infrastructure.google_oidc import STATE_COOKIE
from gateway.interface.dependencies import ContainerDep, PrincipalDep
from gateway.interface.schemas import LoginRequest, RegisterRequest
from gateway.interface.sessions import issue_session
from gateway.log import log

router = APIRouter()


def _start_session(response: Response, container: Any, *, operator_id: str,
                   email: str, is_admin: bool) -> None:
    """One place decides the cookie's shape: HttpOnly, Lax, and how long."""
    response.set_cookie(
        "omg_session",
        issue_session(
            secret=container.settings.session_secret.get_secret_value(),
            operator_id=operator_id,
            email=email,
            is_admin=is_admin,
        ),
        httponly=True,
        # Lax, not Strict: the callback from the provider is cross-site.
        samesite="lax",
        secure=container.settings.base_url.startswith("https://"),
        max_age=60 * 60 * 12,
        path="/",
    )


@router.post("/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response,
                container: ContainerDep) -> dict[str, Any]:
    """The password form. Throttled per (address, email) before the check.

    This is the fallback when the identity provider is broken, which makes it the
    door worth guessing at: without a limit, an attacker with a word list has as
    many attempts as the network allows. The bucket is keyed by both the address
    and the address being tried, so one account under attack does not lock out
    everybody behind the same NAT, and a distributed attempt on one account still
    hits the same bucket once the address is matched.
    """
    limit = container.settings.login_attempts_per_minute
    # Keyed by (peer, address being tried). Keying by the email alone would let
    # anyone lock a known account out by failing on purpose; keying by the peer
    # alone would let one noisy address starve everyone behind the same NAT.
    # A distributed attempt on one account is a different attack and needs a
    # global counter, which this deployment does not have.
    #
    # The limiter is the proxy's own, so a broken Redis degrades both budgets the
    # same way instead of stopping one of them for good.
    if not await container.limiter.allow(_peer(request) or "unknown",
                                         f"login:{payload.email.lower()}", limit):
        log.warning("operator.login_throttled", email=payload.email, ip=_peer(request))
        raise HTTPException(
            status_code=429,
            detail="too many attempts, try again later",
            headers={"Retry-After": "60"},
        )
    operator = await container.commands.authenticate_operator(
        email=payload.email, password=payload.password
    )
    if operator is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    _start_session(response, container, operator_id=operator.id,
                   email=operator.email, is_admin=operator.is_admin)
    return {
        "email": operator.email,
        "is_admin": operator.is_admin,
        "tenant_id": operator.tenant_id,
    }

def _peer(request: Request) -> str | None:
    """The address the limiter should blame. Peer, not a forwarded claim."""
    client = request.scope.get("client")
    return client[0] if client else None


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie("omg_session", path="/")
    return {"status": "logged out"}


@router.get("/auth/me")
async def me(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """Identity, tenant and permissions in one read — what the panel renders from."""
    return await account.identity(container, principal.caller)


@router.post("/auth/register", status_code=201)
async def register(payload: RegisterRequest, response: Response,
                   container: ContainerDep) -> dict[str, Any]:
    """Create an account. Open to anyone — the account starts with no tenant.

    Signing up grants nothing but an identity: a tenant, and the access that comes
    with it, is a separate choice (`POST /api/tenants`, or joining with a code).
    An empty account is a safe thing to hand out.
    """
    operator_id = await container.commands.register_operator(
        email=payload.email, password=payload.password, tenant_id=None
    )
    # Signed in straight away: the next screen is the tenant choice, and making
    # them type the password again to reach it is friction with no benefit.
    _start_session(response, container, operator_id=operator_id,
                   email=payload.email, is_admin=False)
    return {"operator_id": operator_id, "tenant_id": None, "is_admin": False}


@router.get("/auth/methods")
async def auth_methods(container: ContainerDep) -> dict[str, Any]:
    return signin.methods(container)


@router.get("/auth/google/start")
async def google_start(container: ContainerDep) -> RedirectResponse:
    """Send the browser to Google, with a state bound to this browser.

    The state lives in a signed cookie so the callback can prove the browser
    asking is the browser that started. Without it a callback is unauthenticated
    input that ends in a session.
    """
    started = await signin.start(container)
    response = RedirectResponse(started.url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        started.state,
        httponly=True,
        samesite="lax",
        secure=container.settings.base_url.startswith("https://"),
        max_age=started.ttl_seconds,
        path="/api/auth/google",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(request: Request, container: ContainerDep,
                          code: str = "", state: str = "", error: str = "",
                          ) -> Response:
    """Verify what the provider vouched for, then start a session."""
    try:
        signed_in = await signin.complete(
            container,
            cookie_state=request.cookies.get(STATE_COOKIE) or "",
            state=state,
            code=code,
            error=error,
        )
    except signin.SignInRefused as exc:
        # A JSON error would leave the browser staring at an API document; the
        # login page is where the person can act on it.
        log.info("signin.google_refused", reason=str(exc))
        return RedirectResponse(f"/#/login?error={quote(str(exc))}", status_code=303)

    response = RedirectResponse("/#/gateways", status_code=303)
    _start_session(response, container, operator_id=signed_in.operator_id,
                   email=signed_in.email, is_admin=signed_in.is_admin)
    response.delete_cookie(STATE_COOKIE, path="/api/auth/google")
    return response


__all__ = ["router"]
