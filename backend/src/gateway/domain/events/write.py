"""Write-side events: the ones that change an aggregate.

These are the events the command handler appends. Read models consume them too
(see `read.py`), but their identity lives here: they are the domain's vocabulary,
so renaming one is a domain change, not a reporting change.
"""

from pydantic import Field

from gateway.domain.events.base import AuthMode, Event, IdentityProvider

# --------------------------------------------------------------------------
# Operator accounts (who configures the gateway)
# --------------------------------------------------------------------------


class OperatorRegistered(Event):
    EVENT = "operator.registered"

    email: str
    password_hash: str
    is_admin: bool = False


class OperatorLoggedIn(Event):
    EVENT = "operator.logged_in"

    ip: str | None = None


# --------------------------------------------------------------------------
# Gateways — a named proxy you point clients at
# --------------------------------------------------------------------------


class GatewayCreated(Event):
    EVENT = "gateway.created"

    slug: str
    name: str
    target_url: str
    auth_mode: AuthMode = AuthMode.NONE
    idp: IdentityProvider | None = None
    description: str = ""


class GatewayUpdated(Event):
    EVENT = "gateway.updated"

    name: str | None = None
    target_url: str | None = None
    description: str | None = None


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


class GatewayRateLimitSet(Event):
    EVENT = "gateway.rate_limit_set"

    requests_per_minute: int


class GatewayDisabled(Event):
    EVENT = "gateway.disabled"

    reason: str = ""


class GatewayEnabled(Event):
    EVENT = "gateway.enabled"


class GatewayDeleted(Event):
    EVENT = "gateway.deleted"


# --------------------------------------------------------------------------
# Access control — who may call a gateway
# --------------------------------------------------------------------------


class PrincipalAllowed(Event):
    """Grant a subject access to a gateway.

    `subject` is matched against the authenticated identity: the GitHub login,
    the OIDC `email`/`sub`, or "*" for any authenticated principal.
    """

    EVENT = "principal.allowed"

    subject: str
    note: str = ""


class PrincipalRevoked(Event):
    EVENT = "principal.revoked"

    subject: str


# --------------------------------------------------------------------------
# API keys — the non-OAuth way to authenticate
# --------------------------------------------------------------------------


class ApiKeyIssued(Event):
    EVENT = "api_key.issued"

    key_id: str
    label: str
    key_hash: str  # sha256 hex; the plaintext is shown once and never stored
    subject: str = "api-key"


class ApiKeyRevoked(Event):
    EVENT = "api_key.revoked"

    key_id: str


# --------------------------------------------------------------------------
# Sign-in settings — how an operator logs in to the panel itself
# --------------------------------------------------------------------------
#
# A single-instance aggregate (`SIGN_IN_AGGREGATE`), because a deployment has one
# admin panel. Configuring it here rather than through environment variables is
# what lets an administrator turn Google sign-in on without a redeploy — the
# deployment has no idea what client id the customer will use.

SIGN_IN_AGGREGATE = "signin"


class SignInConfigured(Event):
    EVENT = "signin.configured"

    # Empty strings mean "not configured" rather than absent, so a later event can
    # clear a field without a second event type.
    google_client_id: str = ""
    # Write-only, like every other secret in this log.
    google_client_secret: str = ""
    # Must be exactly what was registered at the provider, or the exchange fails
    # with an error the user sees in a browser and the operator sees nowhere.
    redirect_uri: str = ""
    operator_allowlist: tuple[str, ...] = Field(default_factory=tuple)


WRITE_EVENTS: tuple[type[Event], ...] = (
    OperatorRegistered,
    OperatorLoggedIn,
    GatewayCreated,
    GatewayUpdated,
    GatewayAuthConfigured,
    GatewayRateLimitSet,
    GatewayDisabled,
    GatewayEnabled,
    GatewayDeleted,
    PrincipalAllowed,
    PrincipalRevoked,
    ApiKeyIssued,
    ApiKeyRevoked,
    SignInConfigured,
)

__all__ = [
    "SIGN_IN_AGGREGATE",
    "WRITE_EVENTS",
    "ApiKeyIssued",
    "ApiKeyRevoked",
    "GatewayAuthConfigured",
    "GatewayCreated",
    "GatewayDeleted",
    "GatewayDisabled",
    "GatewayEnabled",
    "GatewayRateLimitSet",
    "GatewayUpdated",
    "OperatorLoggedIn",
    "OperatorRegistered",
    "PrincipalAllowed",
    "PrincipalRevoked",
    "SignInConfigured",
]
