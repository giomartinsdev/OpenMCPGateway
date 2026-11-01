"""Domain events — the only source of truth for gateway state.

Every mutation in OpenMCPGateway is an immutable event appended to the store.
Current state is derived by folding a stream; nothing is updated in place.
Event names and payload keys are a public contract: they appear on the wire in
exports and in the audit trail, so rename with a migration, never in place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar


def _enum(value: Any) -> Any:
    """Serialize an enum member, tolerating a plain value after rehydration."""
    return value.value if isinstance(value, StrEnum) else value


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AuthMode(StrEnum):
    """How a caller proves identity to a gateway route."""

    NONE = "none"  # public route — no credentials required
    API_KEY = "api_key"  # static bearer/header key issued by the gateway
    OAUTH = "oauth"  # OAuth proxy to an upstream IdP (github, google, azure, oidc)


class IdentityProvider(StrEnum):
    """Upstream identity providers the OAuth proxy can delegate to."""

    GITHUB = "github"
    GOOGLE = "google"
    AZURE = "azure"
    DISCORD = "discord"
    OIDC = "oidc"  # any discovery-document-compliant issuer


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base event. `aggregate_id` + `sequence` locate it in a stream."""

    EVENT: ClassVar[str] = ""

    aggregate_id: str
    sequence: int = 0
    event_id: str = field(default_factory=lambda: _id("evt"))
    occurred_at: datetime = field(default_factory=_now)
    actor: str = "system"

    def payload(self) -> dict[str, Any]:
        """Serializable body — the fields beyond the envelope."""
        return {}

    def as_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "event_type": self.EVENT,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "payload": self.payload(),
        }


# --------------------------------------------------------------------------
# Operator accounts (who configures the gateway)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorRegistered(Event):
    EVENT = "operator.registered"

    email: str
    password_hash: str
    is_admin: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "password_hash": self.password_hash,
            "is_admin": self.is_admin,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorLoggedIn(Event):
    EVENT = "operator.logged_in"

    ip: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"ip": self.ip}


# --------------------------------------------------------------------------
# Gateways — a named proxy you point clients at
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayCreated(Event):
    EVENT = "gateway.created"

    slug: str
    name: str
    target_url: str
    auth_mode: AuthMode = AuthMode.NONE
    idp: IdentityProvider | None = None
    description: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "target_url": self.target_url,
            "auth_mode": _enum(self.auth_mode),
            "idp": _enum(self.idp) if self.idp else None,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayUpdated(Event):
    EVENT = "gateway.updated"

    name: str | None = None
    target_url: str | None = None
    description: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target_url": self.target_url,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayAuthConfigured(Event):
    EVENT = "gateway.auth_configured"

    auth_mode: AuthMode
    idp: IdentityProvider | None = None
    # OAuth proxy upstream credentials. Never leaves the server in read models.
    client_id: str | None = None
    client_secret: str | None = None
    config_url: str | None = None
    required_scopes: tuple[str, ...] = ()
    # Credential the *upstream* MCP server demands, if any. A gateway in front of
    # a server that already has its own token has to present it; otherwise the
    # only options are "leave the upstream open" or "keep the token in every
    # client config", which is the problem the gateway exists to remove.
    upstream_token: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "auth_mode": _enum(self.auth_mode),
            "idp": _enum(self.idp) if self.idp else None,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "config_url": self.config_url,
            "required_scopes": list(self.required_scopes),
            "upstream_token": self.upstream_token,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayRateLimitSet(Event):
    EVENT = "gateway.rate_limit_set"

    requests_per_minute: int

    def payload(self) -> dict[str, Any]:
        return {"requests_per_minute": self.requests_per_minute}


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayDisabled(Event):
    EVENT = "gateway.disabled"

    reason: str = ""

    def payload(self) -> dict[str, Any]:
        return {"reason": self.reason}


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayEnabled(Event):
    EVENT = "gateway.enabled"

    def payload(self) -> dict[str, Any]:
        return {}


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayDeleted(Event):
    EVENT = "gateway.deleted"

    def payload(self) -> dict[str, Any]:
        return {}


# --------------------------------------------------------------------------
# Access control — who may call a gateway
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class PrincipalAllowed(Event):
    """Grant a subject access to a gateway.

    `subject` is matched against the authenticated identity: the GitHub login,
    the OIDC `email`/`sub`, or "*" for any authenticated principal.
    """

    EVENT = "principal.allowed"

    subject: str
    note: str = ""

    def payload(self) -> dict[str, Any]:
        return {"subject": self.subject, "note": self.note}


@dataclass(frozen=True, slots=True, kw_only=True)
class PrincipalRevoked(Event):
    EVENT = "principal.revoked"

    subject: str

    def payload(self) -> dict[str, Any]:
        return {"subject": self.subject}


# --------------------------------------------------------------------------
# API keys — the non-OAuth way to authenticate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ApiKeyIssued(Event):
    EVENT = "api_key.issued"

    key_id: str
    label: str
    key_hash: str  # sha256 hex; the plaintext is shown once and never stored
    subject: str = "api-key"

    def payload(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "key_hash": self.key_hash,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ApiKeyRevoked(Event):
    EVENT = "api_key.revoked"

    key_id: str

    def payload(self) -> dict[str, Any]:
        return {"key_id": self.key_id}


# --------------------------------------------------------------------------
# Traffic observations — the read side of every proxied call
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestObserved(Event):
    EVENT = "request.observed"

    gateway_id: str
    subject: str
    outcome: str  # allowed | unauthenticated | forbidden | rate_limited | error
    status: int
    duration_ms: int
    ip: str | None = None
    tool: str | None = None
    method: str = "POST"

    def payload(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "subject": self.subject,
            "outcome": self.outcome,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "ip": self.ip,
            "tool": self.tool,
            "method": self.method,
        }
