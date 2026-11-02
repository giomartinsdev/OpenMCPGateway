"""Write-side events: the ones that change an aggregate.

These are the events the command handler appends. Read models consume them too
(see `read.py`), but their identity lives here: they are the domain's vocabulary,
so renaming one is a domain change, not a reporting change.
"""

from pydantic import Field

from gateway.domain.events.base import AuthMode, Event, IdentityProvider

TENANT_OF_DEPLOYMENT = "tn_local"
"""The tenant that accounts and gateways written before tenants existed belong to.

An event is history and is never rewritten, so streams that predate tenancy have
no tenant field at all. This constant is what their absence means: everything
that used to be the installation is now one tenant, and new accounts name their
own (or none, until they create or join one).
"""

# --------------------------------------------------------------------------
# Operator accounts (who configures the gateway)
# --------------------------------------------------------------------------
#
# `is_admin` is the tenant role: the first account administers the installation,
# every account added after it is a member. The tenant id itself is derived
# (`TENANT_OF_DEPLOYMENT`), not stored, so accounts written before tenants
# existed belong to it without a migration and there is no second id to
# disagree with it.

class OperatorRegistered(Event):
    EVENT = "operator.registered"

    email: str
    password_hash: str
    # Kept because streams written before roles existed carry only this, and an
    # event is history: it is read, never rewritten. `role_ids` wins when set.
    is_admin: bool = False
    role_ids: tuple[str, ...] = ()
    # The default is the legacy tenant, not "none": a stream that predates
    # tenancy has no field here, and reading that as "no tenant" would drop every
    # existing account into onboarding. A new signup says `None` explicitly.
    tenant_id: str | None = TENANT_OF_DEPLOYMENT


class OperatorRolesAssigned(Event):
    """Replace an account's roles. The whole set, not a delta: a delta cannot
    express "revoke everything", which is the case that has to work."""

    EVENT = "operator.roles_assigned"

    role_ids: tuple[str, ...] = ()


class OperatorJoinedTenant(Event):
    """An account picks a tenant. The roles come with it: joining is what grants
    the first ones, and it is the only moment where both facts must agree."""

    EVENT = "operator.joined_tenant"

    tenant_id: str
    role_ids: tuple[str, ...] = ()


class TenantCreated(Event):
    EVENT = "tenant.created"

    name: str
    # Uppercase, unambiguous alphabet (no O/0, I/1). This string is read aloud
    # and retyped, so every pair that can be confused is a support ticket.
    join_code: str = ""


class RoleDefined(Event):
    EVENT = "role.defined"

    tenant_id: str = TENANT_OF_DEPLOYMENT
    name: str
    permissions: tuple[str, ...] = ()


class RoleDeleted(Event):
    EVENT = "role.deleted"


class OperatorLoggedIn(Event):
    EVENT = "operator.logged_in"

    ip: str | None = None


# --------------------------------------------------------------------------
# Gateways — a named proxy you point clients at
# --------------------------------------------------------------------------


class GatewayCreated(Event):
    EVENT = "gateway.created"

    tenant_id: str = TENANT_OF_DEPLOYMENT
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
    OperatorRolesAssigned,
    OperatorJoinedTenant,
    TenantCreated,
    OperatorLoggedIn,
    RoleDefined,
    RoleDeleted,
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
    "TENANT_OF_DEPLOYMENT",
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
    "OperatorJoinedTenant",
    "OperatorLoggedIn",
    "OperatorRegistered",
    "OperatorRolesAssigned",
    "PrincipalAllowed",
    "PrincipalRevoked",
    "RoleDefined",
    "RoleDeleted",
    "SignInConfigured",
    "TenantCreated",
]
