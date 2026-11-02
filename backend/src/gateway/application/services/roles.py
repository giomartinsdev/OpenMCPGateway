"""Role lookups, role resolution, and the delegation rule.

Three things were tangled in the HTTP layer before, and none of them is HTTP:
reading the tenant's roles (built-ins + custom, with holder counts), turning
role ids into permissions, and deciding whether a caller may hand a set out.
"""

from collections.abc import Iterable
from typing import Any

from gateway.application.principal import Caller
from gateway.domain.model import DomainError
from gateway.domain.permissions import (
    BUILTIN_ROLES,
    DEFAULT_ROLE_IDS,
    catalog_with_builtins,
    expand,
)


class CannotGrant(PermissionError):
    """The caller tried to hand out a permission it does not hold itself.

    Carries `beyond` so the edge can name the permissions instead of recomputing
    the difference.
    """

    def __init__(self, beyond: Iterable[str]) -> None:
        self.beyond = tuple(beyond)
        super().__init__(
            f"you cannot grant permission(s) you do not hold: "
            f"{', '.join(self.beyond)}"
        )


class RoleCatalog:
    """Read side of roles, for one tenant at a time.

    A small collaborator rather than four free functions taking `container`:
    every caller needs the same three reads, and threading `(container, tenant)`
    through each of them is the kind of signature that invites a missing filter.
    """

    def __init__(self, container: Any) -> None:
        self._container = container

    async def all(self, tenant_id: str | None) -> list[dict[str, Any]]:
        """Built-ins first, then the tenant's own, permissions expanded.

        Holder counts come along because the panel shows them next to the delete
        button, and "why can I not delete this" answered in the same table beats a
        second round trip that fails with a 422.
        """
        holder_counts = (
            await self._container.read_models.role_holder_counts(tenant_id)
            if tenant_id
            else {}
        )
        roles = [
            {
                "id": role_id,
                "name": name,
                "permissions": list(perms),
                "builtin": True,
                "holders": holder_counts.get(role_id, 0),
            }
            for role_id, (name, perms) in BUILTIN_ROLES.items()
        ]
        roles += [
            {
                "id": role["id"],
                "name": role["name"],
                "permissions": list(role["permissions"]),
                "builtin": False,
                "holders": holder_counts.get(role["id"], 0),
            }
            for role in await self._container.read_models.list_roles(tenant_id)
        ]
        return roles

    async def catalog(self, tenant_id: str | None) -> dict[str, tuple[str, ...]]:
        """Role id -> permissions, built-ins included. What `expand` needs."""
        return catalog_with_builtins(
            await self._container.read_models.role_permissions(tenant_id)
            if tenant_id
            else {}
        )

    async def resolve(self, tenant_id: str | None,
                      role_ids: list[str]) -> tuple[str, ...]:
        """Validate role ids against what exists, defaulting to the viewer role.

        Unknown ids are refused rather than dropped: silently granting less than
        the administrator asked for is the failure that gets discovered as "the
        new hire cannot do their job", days later.
        """
        if not role_ids:
            return DEFAULT_ROLE_IDS
        known = {role["id"] for role in await self.all(tenant_id)}
        unknown = sorted(set(role_ids) - known)
        if unknown:
            raise DomainError(
                f"unknown role(s): {', '.join(unknown)} — create them under Roles "
                f"first"
            )
        return tuple(dict.fromkeys(role_ids))

    async def permissions_of(self, tenant_id: str | None,
                             role_ids: Iterable[str]) -> tuple[str, ...]:
        return expand(role_ids, await self.catalog(tenant_id))

    def ensure_grantable(self, caller: Caller, permissions: Iterable[str],
                         default: Iterable[str] = ()) -> None:
        """Refuse to hand out a permission the caller does not hold itself.

        Without this, `members:write` is a path to `settings:write`: add an
        account, log in as it, done. Delegation may only narrow — an
        administrator who can do everything can grant anything, and everyone else
        grants a subset of their own.

        `default` is exempt: the default role is the floor a new account lands
        on, not a choice the caller made. A recruiter who may add people but not
        read gateways still must be able to create an account, exactly as
        registration would have.
        """
        beyond = sorted(set(permissions) - caller.permissions - set(default))
        if beyond:
            raise CannotGrant(beyond)


__all__ = ["CannotGrant", "RoleCatalog"]
