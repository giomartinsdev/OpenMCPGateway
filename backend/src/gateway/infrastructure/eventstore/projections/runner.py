"""Applies events to read models, tracking its own checkpoint."""



from gateway.domain.events import (
    ApiKeyIssued,
    ApiKeyRevoked,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayRateLimitSet,
    GatewayUpdated,
    OperatorJoinedTenant,
    OperatorRegistered,
    OperatorRolesAssigned,
    PrincipalAllowed,
    PrincipalRevoked,
    RequestObserved,
    RoleDefined,
    RoleDeleted,
    TenantCreated,
)
from gateway.infrastructure.db.database import Database
from gateway.infrastructure.eventstore.projections import handlers


class ProjectionRunner:
    """Applies events to read models, tracking its own checkpoint."""

    name = "read_models"

    def __init__(self, database: Database) -> None:
        self._db = database
        self._handlers: dict[str, handlers.Handler] = {
            GatewayCreated.EVENT: handlers.on_gateway_created,
            GatewayUpdated.EVENT: handlers.on_gateway_updated,
            GatewayAuthConfigured.EVENT: handlers.on_auth_configured,
            GatewayRateLimitSet.EVENT: handlers.on_rate_limit,
            GatewayDisabled.EVENT: handlers.on_disabled,
            GatewayEnabled.EVENT: handlers.on_enabled,
            GatewayDeleted.EVENT: handlers.on_deleted,
            PrincipalAllowed.EVENT: handlers.on_principal_allowed,
            PrincipalRevoked.EVENT: handlers.on_principal_revoked,
            ApiKeyIssued.EVENT: handlers.on_key_issued,
            ApiKeyRevoked.EVENT: handlers.on_key_revoked,
            RequestObserved.EVENT: handlers.on_request,
            OperatorRegistered.EVENT: handlers.on_operator_registered,
            OperatorRolesAssigned.EVENT: handlers.on_roles_assigned,
            OperatorJoinedTenant.EVENT: handlers.on_joined_tenant,
            TenantCreated.EVENT: handlers.on_tenant_created,
            RoleDefined.EVENT: handlers.on_role_defined,
            RoleDeleted.EVENT: handlers.on_role_deleted,
        }

    async def apply(self, global_seq: int, event: Event) -> None:
        del global_seq  # Ordering is the log's job; handlers are idempotent.
        handler = self._handlers.get(event.EVENT)
        if handler is None:
            return
        async with self._db.session() as session, session.begin():
            await handler(session, event)


__all__ = ["ProjectionRunner"]
