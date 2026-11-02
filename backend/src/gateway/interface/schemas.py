"""Request and response bodies for the admin API.

Pydantic models only: no defaults that grant access, no validation that belongs
to the domain. `Field(min_length=12)` on a password is a transport rule; "is this
email already taken" is a domain rule and lives in the command handler.
"""

from typing import Any

from pydantic import BaseModel, Field

from gateway.domain.events import AuthMode, IdentityProvider


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
