"""A tenant: the boundary that owns gateways, roles and accounts.

Before tenancy existed this was a constant — one deployment, one tenant, so
"sign up" meant "join the installation". The join code is the whole membership
flow: there is no invitation email to send from a process with no mail server.
"""

from collections.abc import Iterable

from gateway.domain.events import Event, TenantCreated
from gateway.domain.model.base import DomainError, _Aggregate

JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""No O/0/I/1: the code is read aloud and typed from a screenshot."""

class Tenant(_Aggregate):
    id: str = ""
    name: str = ""
    join_code: str = ""
    exists: bool = False

    @classmethod
    def from_events(cls, aggregate_id: str, events: Iterable[Event]) -> Tenant:
        state = cls(id=aggregate_id)
        for event in events:
            if isinstance(event, TenantCreated):
                state.name = event.name
                state.join_code = event.join_code
                state.exists = True
        return state

    def decide_create(self, *, name: str, join_code: str) -> list[Event]:
        if self.exists:
            raise DomainError("tenant already exists")
        clean = name.strip()
        if not clean:
            raise DomainError("tenant name is required")
        if len(clean) > 64:
            raise DomainError("tenant name must be 64 characters or fewer")
        if not join_code or len(join_code) < 8:
            raise DomainError("join code must be at least 8 characters")
        return [
            TenantCreated(
                aggregate_id=self.id, name=clean, join_code=join_code.upper()
            )
        ]
