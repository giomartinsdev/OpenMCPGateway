"""Sign-in settings, and the Google flow as a use case.

Two things here that are not HTTP: the panel's settings win once anybody saves
them, otherwise the environment seeds a fresh deployment; and a verified provider
identity becomes an account, or reuses the one that already exists. The route
only sets a cookie and redirects.
"""

import hmac
from dataclasses import dataclass
from typing import Any

from gateway.application.container import Container
from gateway.domain.events import TENANT_OF_DEPLOYMENT
from gateway.domain.model import DomainError, Operator, SignInSettings
from gateway.domain.permissions import DEFAULT_ROLE_IDS
from gateway.infrastructure.google_oidc import (
    STATE_TTL_SECONDS,
    GoogleOIDC,
    LoginError,
    issue_state,
    read_state,
)


class SignInRefused(Exception):
    """Sign-in is off, or the callback did not verify. Shown on the login page."""


@dataclass(frozen=True)
class Started:
    """Where to send the browser, and what to remember about the request."""

    url: str
    state: str
    ttl_seconds: int = STATE_TTL_SECONDS


@dataclass(frozen=True)
class SignedIn:
    """A verified account, ready for a session cookie."""

    operator_id: str
    email: str
    is_admin: bool


async def settings(container: Container) -> SignInSettings:
    """Panel settings, seeded from the environment on first boot.

    The environment is how a fresh deployment gets in; after that the panel owns
    the configuration, because the client id belongs to whoever runs it.
    """
    stored = await container.commands.read_sign_in_settings()
    if stored.version > 0:
        return stored
    env = container.settings
    if not env.google_login_enabled:
        return stored
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


def public(current: SignInSettings) -> dict[str, Any]:
    return {
        "google_client_id": current.google_client_id,
        "redirect_uri": current.redirect_uri,
        "operator_allowlist": list(current.operator_allowlist),
        "enabled": current.enabled,
        # Presence only. A secret that round-trips to the browser ends up in a
        # screenshot, a HAR file and an issue report.
        "has_google_client_secret": bool(current.google_client_secret),
    }


async def methods(container: Container) -> dict[str, Any]:
    """What the login page may offer. A password form always exists."""
    current = await settings(container)
    return {
        "password": True,
        "google": current.enabled,
        # Whether a deployment already knows some addresses. The login page uses
        # it to explain the difference; it is not a gate.
        "allowlist_configured": bool(current.operator_allowlist),
    }


async def save(container: Container, *, google_client_id: str,
               google_client_secret: str, redirect_uri: str,
               operator_allowlist: list[str], actor: str) -> SignInSettings:
    await container.commands.configure_sign_in(
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        redirect_uri=redirect_uri,
        operator_allowlist=tuple(operator_allowlist),
        actor=actor,
    )
    return await settings(container)


async def provider(container: Container) -> GoogleOIDC:
    current = await settings(container)
    if not current.enabled:
        raise SignInRefused(
            "Google sign-in is not configured — set it under Settings in the panel"
        )
    return GoogleOIDC(
        client_id=current.google_client_id,
        client_secret=current.google_client_secret,
        discovery_url=container.settings.google_discovery_url,
        redirect_uri=current.redirect_uri,
    )


async def is_known_address(container: Container, email: str) -> bool:
    """True when this address was listed as already belonging to the deployment.

    The allowlist is an address book, not a gate: a listed address skips the
    tenant choice and lands in the installation's own tenant. Everyone else gets
    an account and no tenant.
    """
    current = await settings(container)
    wanted = email.strip().lower()
    return wanted in {entry.lower() for entry in current.operator_allowlist}


async def start(container: Container) -> Started:
    oidc = await provider(container)
    state = issue_state(
        secret=container.settings.session_secret.get_secret_value(),
        redirect_uri=oidc.redirect_uri,
    )
    return Started(url=await oidc.authorization_url(state=state), state=state)


async def complete(container: Container, *, cookie_state: str, state: str,
                   code: str, error: str) -> SignedIn:
    """Verify the callback and return the account it belongs to.

    Raises `SignInRefused` for anything that is not a verified, stored identity —
    including a state that does not match this browser, which is checked first
    because none of the other parameters can be trusted until it passes.
    """
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise SignInRefused("login state does not match this browser")
    try:
        read_state(state, secret=container.settings.session_secret.get_secret_value())
    except LoginError as exc:
        raise SignInRefused(str(exc)) from exc
    if error:
        raise SignInRefused(f"provider reported: {error}")
    if not code:
        raise SignInRefused("provider returned no code")

    oidc = await provider(container)
    try:
        identity = await oidc.exchange(code=code)
    except Exception as exc:  # every provider failure reads the same to the caller
        raise SignInRefused(str(exc)) from exc

    operator_id = await container.store.fetch_operator_by_email(identity.email)
    if operator_id is None:
        operator_id = await _create(container, identity)
    return await _signed_in(container, operator_id)


async def _create(container: Container, identity: Any) -> str:
    """First sign-in: an identity, plus the deployment's tenant if it knows them.

    Anyone the provider vouches for gets an identity. Without a tenant it can
    read nothing until it creates one or joins with a code, which is what makes
    an open sign-up safe.
    """
    known = await is_known_address(container, identity.email)
    if known:
        await container.commands.ensure_tenant(
            tenant_id=TENANT_OF_DEPLOYMENT, name="Default"
        )
    try:
        return await container.commands.register_sso_operator(
            email=identity.email,
            subject=identity.subject,
            tenant_id=TENANT_OF_DEPLOYMENT if known else None,
            role_ids=DEFAULT_ROLE_IDS if known else None,
        )
    except DomainError as exc:
        # Two tabs signing in at once: the other one created it. Reuse it.
        existing = await container.store.fetch_operator_by_email(identity.email)
        if existing is None:
            raise SignInRefused(str(exc)) from exc
        return existing


async def _signed_in(container: Container, operator_id: str) -> SignedIn:
    events = await container.store.load_stream(operator_id)
    operator = Operator.from_events(operator_id, events)
    return SignedIn(
        operator_id=operator_id, email=operator.email, is_admin=operator.is_admin
    )


__all__ = [
    "SignInRefused",
    "SignedIn",
    "Started",
    "complete",
    "is_known_address",
    "methods",
    "provider",
    "public",
    "save",
    "settings",
    "start",
]
