"""Write-side events: the ones that change an aggregate.

These are the events the command handler appends. Read models consume them too
(see `read.py`), but their identity lives here: they are the domain's vocabulary,
so renaming one is a domain change, not a reporting change.
"""

from datetime import datetime

from pydantic import Field

from gateway.domain.events.base import (
    AuthMode,
    Event,
    IdentityProvider,
    VerifierSpec,
)

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
    #: Accept API keys *as well as* the OAuth login above. One route, two ways
    #: in: a browser client logs in, a script presents a key.
    allow_api_key: bool = False
    #: Additional issuers whose tokens this gateway verifies without hosting
    #: their login. Empty means "only the login above".
    verifiers: tuple[VerifierSpec, ...] = ()
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
    key_hash: str  # sha256 hex; what the proxy compares against
    subject: str = "api-key"
    # The plaintext, sealed with the deployment secret, so the operator who
    # issued it can still read it once from the panel. Empty for keys minted
    # before sealing existed, and wiped the moment the key is revealed.
    sealed: str = ""
    #: When this key stops being accepted. `None` means never, which is what
    #: every key issued before this field existed gets — a key that silently
    #: expired on upgrade would take a production integration down.
    expires_at: datetime | None = None


class ApiKeyRevealed(Event):
    """The plaintext was handed back to an operator.

    A fact worth recording: it is the last moment the credential can be read,
    and the audit trail should say who did it and when.
    """

    EVENT = "api_key.revealed"

    key_id: str


class ApiKeyRevoked(Event):
    EVENT = "api_key.revoked"

    key_id: str


class GatewayToolsConfigured(Event):
    """Which upstream tools this gateway exposes. Empty means all of them.

    `source` scopes the list to one credential source — `google`, a verifier's
    label, `api_key`. Empty is the gateway-wide list every source inherits,
    which is what every gateway had before scopes existed. A call for a source
    with no list of its own falls back to this one; an empty list for a source
    removes it, so "no exception" and "no list" are the same fact.
    """

    EVENT = "gateway.tools_configured"

    tools: tuple[str, ...] = ()
    source: str = ""

class GatewayQuotaSet(Event):
    """A monthly call ceiling, separate from the per-minute rate limit.

    A rate limit bounds a burst; an integration that calls politely all month is
    invisible to it. Zero means no ceiling, the same convention as the limit,
    because two spellings of "unlimited" in one panel is a support call.
    """

    EVENT = "gateway.quota_set"

    monthly_quota: int = 0

class GatewayRestored(Event):
    """A gateway was rolled back to the configuration of an earlier version.

    One event carrying the whole restored configuration rather than a replay of
    the commands that produced it: the point of a rollback is that the log says
    "someone rolled this back to version 7", not that it happens to look like a
    rename followed by a rate-limit change. Credentials and keys are not part of
    it — a revoked key must stay revoked, and a restore is not a resurrection.
    """

    EVENT = "gateway.restored"

    restored_from: int
    name: str
    target_url: str
    description: str = ""
    requests_per_minute: int = 0
    tools: tuple[str, ...] = ()
    #: Per-source overrides as they were at `restored_from`. Part of the whole
    #: configuration: a rollback that left a narrow scope behind would say it
    #: restored and then keep refusing the bot's tools.
    tool_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)

class OperatorDisabled(Event):
    """An account may no longer sign in, and its live sessions stop working.

    A signed cookie is a claim about the past, so a revocation has to be a fact
    the server re-reads — not a row deleted from under a still-valid token.
    """

    EVENT = "operator.disabled"

class OperatorEnabled(Event):
    EVENT = "operator.enabled"

# --------------------------------------------------------------------------
# Webhooks — the observation pushed, instead of polled
# --------------------------------------------------------------------------

class WebhookRegistered(Event):
    """A URL to POST observations to, and what it subscribed to.

    `secret` is sealed with the deployment secret: the receiver needs it to
    verify the signature, and the database does not need it in the clear.
    """

    EVENT = "webhook.registered"

    webhook_id: str
    url: str
    secret: str
    #: Empty means every outcome. A hook that asked for failures only must not
    #: receive a success, or the filter is decoration.
    outcomes: tuple[str, ...] = ()

class WebhookDeleted(Event):
    EVENT = "webhook.deleted"

    webhook_id: str


class SignInConfigured(Event):
    """Retired: sign-in is deployment configuration now.

    Kept in the registry because the log is append-only and a replay has to be
    able to read rows it can never write. Nothing folds this any more; removing
    the class would turn an old installation's history into a `KeyError` at boot.
    """

    EVENT = "signin.configured"

    google_client_id: str = ""
    google_client_secret: str = ""
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
    GatewayRestored,
    GatewayToolsConfigured,
    PrincipalAllowed,
    PrincipalRevoked,
    ApiKeyIssued,
    ApiKeyRevealed,
    ApiKeyRevoked,
    SignInConfigured,
    GatewayQuotaSet,
    OperatorDisabled,
    OperatorEnabled,
    WebhookRegistered,
    WebhookDeleted,
)

__all__ = [
    "TENANT_OF_DEPLOYMENT",
    "WRITE_EVENTS",
    "ApiKeyIssued",
    "ApiKeyRevealed",
    "ApiKeyRevoked",
    "GatewayAuthConfigured",
    "GatewayCreated",
    "GatewayDeleted",
    "GatewayDisabled",
    "GatewayEnabled",
    "GatewayQuotaSet",
    "GatewayRateLimitSet",
    "GatewayRestored",
    "GatewayToolsConfigured",
    "GatewayUpdated",
    "OperatorDisabled",
    "OperatorEnabled",
    "OperatorJoinedTenant",
    "OperatorLoggedIn",
    "OperatorRegistered",
    "OperatorRolesAssigned",
    "PrincipalAllowed",
    "PrincipalRevoked",
    "RoleDefined",
    "RoleDeleted",
    "TenantCreated",
    "WebhookDeleted",
    "WebhookRegistered",
]
