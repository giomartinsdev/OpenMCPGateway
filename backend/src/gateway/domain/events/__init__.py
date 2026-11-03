"""Events, split by who consumes them.

* `base` — the envelope and the shared enums
* `write` — events that change an aggregate
* `read` — events that only feed read models

`gateway.domain.events` re-exports everything so call sites do not care which
module a class lives in; the split is about dependency direction, not imports.
"""

from gateway.domain.events.base import AuthMode, Event, IdentityProvider
from gateway.domain.events.read import READ_EVENTS, RequestObserved
from gateway.domain.events.write import (
    TENANT_OF_DEPLOYMENT,
    WRITE_EVENTS,
    ApiKeyIssued,
    ApiKeyRevealed,
    ApiKeyRevoked,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayQuotaSet,
    GatewayRateLimitSet,
    GatewayRestored,
    GatewayToolsConfigured,
    GatewayUpdated,
    OperatorDisabled,
    OperatorEnabled,
    OperatorJoinedTenant,
    OperatorLoggedIn,
    OperatorRegistered,
    OperatorRolesAssigned,
    PrincipalAllowed,
    PrincipalRevoked,
    RoleDefined,
    RoleDeleted,
    TenantCreated,
    WebhookDeleted,
    WebhookRegistered,
)

ALL_EVENTS: tuple[type[Event], ...] = (*WRITE_EVENTS, *READ_EVENTS)

__all__ = [
    "ALL_EVENTS",
    "READ_EVENTS",
    "TENANT_OF_DEPLOYMENT",
    "WRITE_EVENTS",
    "ApiKeyIssued",
    "ApiKeyRevealed",
    "ApiKeyRevoked",
    "AuthMode",
    "Event",
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
    "IdentityProvider",
    "OperatorDisabled",
    "OperatorEnabled",
    "OperatorJoinedTenant",
    "OperatorLoggedIn",
    "OperatorRegistered",
    "OperatorRolesAssigned",
    "PrincipalAllowed",
    "PrincipalRevoked",
    "RequestObserved",
    "RoleDefined",
    "RoleDeleted",
    "TenantCreated",
    "WebhookDeleted",
    "WebhookRegistered",
]
