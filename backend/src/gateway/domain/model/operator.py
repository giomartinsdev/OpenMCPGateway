"""An account: who it is, and which tenant it may act in."""

from collections.abc import Iterable, Sequence

from gateway.domain.events import (
    Event,
    OperatorJoinedTenant,
    OperatorRegistered,
    OperatorRolesAssigned,
)
from gateway.domain.model.base import DomainError, _Aggregate
from gateway.domain.permissions import DEFAULT_ROLE_IDS, OWNER_ROLE


class Operator(_Aggregate):
    id: str = ""
    email: str = ""
    password_hash: str = ""
    is_admin: bool = False
    role_ids: tuple[str, ...] = ()
    #: `None` until the account creates or joins a tenant. An account in that
    #: state can sign in and see the onboarding choice, and nothing else.
    tenant_id: str | None = None
    exists: bool = False

    @property
    def role(self) -> str:
        """The single role name, for the places that predate multiple roles."""
        return "admin" if self.is_admin else "member"

    @property
    def effective_roles(self) -> tuple[str, ...]:
        """Roles after folding, with the legacy `is_admin` flag honoured.

        A stream written before roles existed has only `is_admin`, and an event
        is history: it is read, never rewritten. So the flag decides the role
        when no roles were ever assigned.
        """
        if self.role_ids:
            return self.role_ids
        return (OWNER_ROLE,) if self.is_admin else DEFAULT_ROLE_IDS

    @classmethod
    def from_events(cls, aggregate_id: str, events: Iterable[Event]) -> Operator:
        state = cls(id=aggregate_id)
        for event in events:
            if isinstance(event, OperatorRegistered):
                state.email = event.email
                state.password_hash = event.password_hash
                state.is_admin = event.is_admin
                state.role_ids = tuple(event.role_ids)
                state.tenant_id = event.tenant_id
                state.exists = True
            elif isinstance(event, OperatorRolesAssigned):
                state.role_ids = tuple(event.role_ids)
            elif isinstance(event, OperatorJoinedTenant):
                state.tenant_id = event.tenant_id
                state.role_ids = tuple(event.role_ids)
        return state

    def decide_assign_roles(self, role_ids: Sequence[str]) -> list[Event]:
        if not self.exists:
            raise DomainError("operator does not exist")
        return [
            OperatorRolesAssigned(
                aggregate_id=self.id, role_ids=tuple(dict.fromkeys(role_ids))
            )
        ]

    def decide_join_tenant(
        self, *, tenant_id: str, role_ids: Sequence[str]
    ) -> list[Event]:
        if not self.exists:
            raise DomainError("operator does not exist")
        if self.tenant_id == tenant_id:
            raise DomainError("this account already belongs to that tenant")
        if self.tenant_id is not None:
            # Leaving would be a different command with a different question
            # (what happens to the gateways), not a side effect of joining.
            raise DomainError(
                "this account already belongs to a tenant; leaving is not supported"
            )
        if not tenant_id:
            raise DomainError("tenant_id is required")
        return [
            OperatorJoinedTenant(
                aggregate_id=self.id,
                tenant_id=tenant_id,
                role_ids=tuple(dict.fromkeys(role_ids)),
            )
        ]

    def decide_register(
        self, *, email: str, password_hash: str, is_admin: bool = False,
        role_ids: Sequence[str] = (), tenant_id: str | None = None,
    ) -> list[Event]:
        if self.exists:
            raise DomainError("operator already registered")
        if "@" not in email:
            raise DomainError("invalid email")
        if not password_hash:
            raise DomainError("password hash required")
        return [
            OperatorRegistered(
                aggregate_id=self.id,
                email=email,
                password_hash=password_hash,
                is_admin=is_admin,
                role_ids=tuple(dict.fromkeys(role_ids)),
                tenant_id=tenant_id,
            )
        ]
