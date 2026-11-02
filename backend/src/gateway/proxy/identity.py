"""Who is calling a gateway, and whether that is good enough.

`Identity` is the result of a check, not state: it is frozen because a request
must not be able to talk itself into a different subject halfway through.
"""

from typing import Any

from fastmcp.server.auth import AuthProvider
from pydantic import BaseModel, ConfigDict

from gateway.domain.events import AuthMode
from gateway.infrastructure.eventstore.projections import ReadModels


class Identity(BaseModel):
    """Who is calling. Immutable: it is the result of a check, not state."""

    model_config = ConfigDict(frozen=True)

    subject: str
    display: str = ""
    method: str = "anonymous"


class IdentityDenied(LookupError):
    """Credential was present but rejected."""


class GatewayNotReady(RuntimeError):
    """The gateway is configured but cannot serve yet (missing credentials).

    A draft in this state is kept out of the route table entirely, so the route
    answers 404 instead of becoming an accidentally public proxy.
    """


class GatewayAuth:
    """Resolves a bearer token into an Identity for one gateway.

    For OAuth gateways this delegates to the FastMCP provider's own
    `verify_token`, so there is exactly one implementation of "is this token
    good" and the proxy layer cannot drift from the transport layer.
    """

    def __init__(self, config: dict[str, Any], read_models: ReadModels,
                 provider: AuthProvider | None) -> None:
        self._config = config
        self._read_models = read_models
        self._provider = provider
        self.mode = AuthMode(config["auth_mode"])

    async def resolve(self, token: str | None) -> Identity | None:
        """Return the caller's identity, or None when no credential is valid."""
        if self.mode is AuthMode.NONE:
            return Identity(subject="anonymous", method="none")
        if not token:
            return None
        if self.mode is AuthMode.API_KEY:
            from gateway.infrastructure.security import hash_api_key

            subject = await self._read_models.subject_for_key_hash(
                self._config["id"], hash_api_key(token)
            )
            return Identity(subject=subject, method="api_key") if subject else None
        if self._provider is None:
            return None
        access = await self._provider.verify_token(token)
        if access is None:
            return None
        claims = getattr(access, "claims", {}) or {}
        # Prefer a human-stable identifier so allowlists stay readable.
        subject = (
            claims.get("login")
            or claims.get("email")
            or claims.get("preferred_username")
            or access.subject
            or access.client_id
        )
        return Identity(
            subject=str(subject),
            display=str(claims.get("name") or claims.get("email") or ""),
            method=self.mode.value,
        )
