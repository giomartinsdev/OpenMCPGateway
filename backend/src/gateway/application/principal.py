"""Who is asking, as the application layer sees it.

The HTTP layer knows about cookies and `HTTPException`; the use cases below it
should not. `Caller` is the smallest thing they need — an id, an address, the
tenant the answer is scoped to, and the permissions already resolved — so a
service can be called from a test, a CLI or a worker without a request.

`tenant_id` is `None` for an account that has not created or joined a tenant yet.
That is a real state, not an error: it is what onboarding is for.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Caller:
    operator_id: str
    email: str
    is_admin: bool = False
    tenant_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    @property
    def onboarded(self) -> bool:
        return self.tenant_id is not None

    @property
    def tenant(self) -> str:
        """The tenant a scoped call belongs to. The caller must have one."""
        if self.tenant_id is None:
            raise NotOnboarded("onboarding: create or join a tenant first")
        return self.tenant_id


class NotOnboarded(Exception):
    """The account has no tenant yet, so there is nothing to scope to."""


__all__ = ["Caller", "NotOnboarded"]
