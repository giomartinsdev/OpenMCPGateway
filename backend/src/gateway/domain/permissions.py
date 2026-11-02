"""The permission vocabulary and the roles that cannot be deleted.

A closed catalog, not free-form strings. A typo in a permission is a role that
grants nothing and says nothing — the member discovers it as a 403 on a button
that was offered. Validating here turns that into a 422 naming the field.

Two roles are built in because every installation needs them and neither is a
policy decision: `owner` administers everything, `viewer` reads the gateways,
their keys, traffic and the audit log — the shape a member had before roles
existed, and nothing wider. Member lists and roles stay a privilege an
administrator grants explicitly.

There is deliberately no permission for the panel's own sign-in configuration:
it is deployment configuration, read from the environment at boot, not something
a tenant owns. A tenant-scoped permission over it would mean any tenant's owner
could reconfigure every tenant's login.
"""

READ_ONLY: tuple[str, ...] = (
    "gateways:read",
    "keys:read",
    "traffic:read",
    "audit:read",
    "config:read",
)

#: Reads an administrator needs and a viewer does not: the address book and the
#: role table.
ADMIN_READS: tuple[str, ...] = (
    "members:read",
    "roles:read",
)

PERMISSIONS: tuple[str, ...] = (
    *READ_ONLY,
    *ADMIN_READS,
    "gateways:write",
    "keys:write",
    "config:write",
    "members:write",
    "roles:write",
)

OWNER_ROLE = "role_owner"
VIEWER_ROLE = "role_viewer"

#: Built-in roles, by id. Not events: they are code, and a deployment that lost
#: its log still has an owner.
BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    OWNER_ROLE: ("owner", PERMISSIONS),
    VIEWER_ROLE: ("viewer", READ_ONLY),
}

#: What a new member gets when the administrator does not name a role. The same
#: access the single "member" role had, so adding roles widens nobody by default.
DEFAULT_ROLE_IDS: tuple[str, ...] = (VIEWER_ROLE,)

class PermissionCatalogError(ValueError):
    """A permission outside the catalog. A validation failure, not a 403."""

def normalise(permissions: object) -> tuple[str, ...]:
    """Validate and de-duplicate, in catalog order.

    Catalog order, not the caller's: two roles with the same permissions then
    serialize identically, which is what makes a diff of the exported
    configuration readable.
    """
    given = {str(p).strip() for p in (permissions or ()) if str(p).strip()}
    unknown = sorted(given - set(PERMISSIONS))
    if unknown:
        raise PermissionCatalogError(
            f"unknown permission(s): {', '.join(unknown)} — "
            f"valid: {', '.join(PERMISSIONS)}"
        )
    return tuple(p for p in PERMISSIONS if p in given)

def expand(role_ids: object, catalog: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Union of the named roles' permissions, in catalog order.

    `catalog` is passed in rather than read from `BUILTIN_ROLES` because custom
    roles live in the event log: the caller (which has a database) owns the
    lookup, and this stays a pure function.
    """
    granted: set[str] = set()
    for role_id in role_ids or ():
        granted.update(catalog.get(str(role_id), ()))
    return tuple(p for p in PERMISSIONS if p in granted)

def catalog_with_builtins(
    custom: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Built-in roles first (so they always exist), then what the tenant defined."""
    merged = {role_id: perms for role_id, (_, perms) in BUILTIN_ROLES.items()}
    merged.update(custom)
    return merged

__all__ = [
    "BUILTIN_ROLES",
    "DEFAULT_ROLE_IDS",
    "OWNER_ROLE",
    "PERMISSIONS",
    "READ_ONLY",
    "VIEWER_ROLE",
    "PermissionCatalogError",
    "catalog_with_builtins",
    "expand",
    "normalise",
]
