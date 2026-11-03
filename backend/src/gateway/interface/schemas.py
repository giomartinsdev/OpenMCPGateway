"""Request and response bodies for the admin API.

Pydantic models only: no defaults that grant access, no validation that belongs
to the domain. `Field(min_length=12)` on a password is a transport rule; "is this
email already taken" is a domain rule and lives in the command handler.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from gateway.domain.events import AuthMode, IdentityProvider, VerifierSpec


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
    #: Accept keys as well as the login above, on the same route.
    allow_api_key: bool = False
    #: Additional issuers verified without hosting their login.
    verifiers: list[VerifierSpec] = Field(default_factory=list)
    # Bearer the upstream MCP server demands. Write-only: never read back.
    upstream_token: str | None = None


class RateLimitRequest(BaseModel):
    requests_per_minute: int = Field(ge=0)


class GatewayToolsRequest(BaseModel):
    #: Empty means every upstream tool. Names are not validated against the
    #: upstream: the write side has no network, and a stale name matching
    #: nothing is visible in the panel next to the real catalog.
    tools: list[str] = Field(default_factory=list)


class PrincipalRequest(BaseModel):
    subject: str
    note: str = ""


class ApiKeyRequest(BaseModel):
    label: str
    #: Days from now until this key stops working. `None` means never.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class QuotaRequest(BaseModel):
    """A calendar-month ceiling. 0 means none, like the rate limit."""

    monthly_quota: int = Field(ge=0, le=100_000_000)


class RestoreRequest(BaseModel):
    """The sequence to roll back to. Refused when it does not exist yet."""

    version: int = Field(ge=1)


class WebhookRequest(BaseModel):
    """A URL to POST observations to, and what it subscribed to."""

    url: str = Field(min_length=1, max_length=2048)
    secret: str = Field(min_length=1, max_length=512)
    #: Empty means every outcome. The names are the guard's own: allowed,
    #: unauthenticated, forbidden, rate_limited, error.
    outcomes: list[str] = Field(default_factory=list)


class CallToolRequest(BaseModel):
    """One tool call, typed by the operator in the panel.

    `arguments` is `Any` on purpose: a tool's schema is the upstream's, and a
    closed model here would reject the second tool anybody adds.
    """

    tool: str = Field(min_length=1, max_length=128)
    arguments: Any = None

    @field_validator("arguments")
    @classmethod
    def _object_arguments(cls, value: Any) -> Any:
        """Arguments are a JSON object, or nothing.

        The panel sends text; a value that is not an object means the operator
        typed something the upstream cannot read. Refusing it here names the
        field, instead of letting the upstream answer with a schema error the
        operator will read as "the tool is broken".
        """
        if value is None or isinstance(value, dict):
            return value
        raise ValueError("arguments must be a JSON object")


class ConfigFileRequest(BaseModel):
    """Either raw YAML text or an already-parsed mapping."""

    yaml: str | None = None
    document: dict[str, Any] | None = None


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TenantJoinRequest(BaseModel):
    code: str = Field(min_length=1)


class SignInSettingsPayload(BaseModel):
    google_client_id: str = ""
    # Write-only: absent means "keep the stored one".
    google_client_secret: str = ""
    redirect_uri: str = ""
    operator_allowlist: list[str] = Field(default_factory=list)


__all__ = [
    "ApiKeyRequest",
    "AuthConfigRequest",
    "ConfigFileRequest",
    "GatewayCreateRequest",
    "GatewayUpdateRequest",
    "LoginRequest",
    "OperatorCreateRequest",
    "PrincipalRequest",
    "RateLimitRequest",
    "RegisterRequest",
    "RoleAssignRequest",
    "RoleRequest",
    "SignInSettingsPayload",
    "TenantCreateRequest",
    "TenantJoinRequest",
]
