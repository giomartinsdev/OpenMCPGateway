"""A named set of permissions.

A role is an aggregate of its own so a permission never has to be rewritten:
editing a role appends, and every account holding it picks up the change on the
next request (the session carries role ids, not permissions).
"""

from collections.abc import Iterable, Sequence

from gateway.domain.events import (
    TENANT_OF_DEPLOYMENT,
    Event,
    RoleDefined,
    RoleDeleted,
)
from gateway.domain.model.base import DomainError, _Aggregate
from gateway.domain.permissions import normalise


class Role(_Aggregate):
    id: str = ""
    tenant_id: str = TENANT_OF_DEPLOYMENT
    name: str = ""
    permissions: tuple[str, ...] = ()
    deleted: bool = False
    exists: bool = False

    @classmethod
    def from_events(cls, aggregate_id: str, events: Iterable[Event]) -> Role:
        state = cls(id=aggregate_id)
        for event in events:
            if isinstance(event, RoleDefined):
                state.tenant_id = event.tenant_id
                state.name = event.name
                state.permissions = tuple(event.permissions)
                state.deleted = False
                state.exists = True
            elif isinstance(event, RoleDeleted):
                state.deleted = True
        return state

    def decide_define(
        self, *, tenant_id: str, name: str, permissions: Sequence[str]
    ) -> list[Event]:
        if self.deleted:
            raise DomainError("role was deleted; define a new one under another name")
        clean_name = name.strip()
        if not clean_name:
            raise DomainError("role name is required")
        if len(clean_name) > 64:
            raise DomainError("role name must be 64 characters or fewer")
        if self.exists and self.tenant_id != tenant_id:
            raise DomainError("role belongs to another tenant")
        return [
            RoleDefined(
                aggregate_id=self.id,
                tenant_id=tenant_id,
                name=clean_name,
                permissions=normalise(permissions),
            )
        ]

    def decide_delete(self) -> list[Event]:
        if not self.exists or self.deleted:
            raise DomainError("role does not exist")
        return [RoleDeleted(aggregate_id=self.id)]
