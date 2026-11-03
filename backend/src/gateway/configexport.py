"""Rendering the current state back into the file format.

The inverse of `parse_document`, and it lives next to it on purpose: a change to
the document shape must change both readers in one diff, or an export stops
round-tripping. Secrets are deliberately omitted — the export is safe to commit,
and the operator re-supplies them via `${ENV}` placeholders.
"""

from typing import Any

from gateway.domain.permissions import BUILTIN_ROLES


def render_gateway(gateway: dict[str, Any], principals: list[dict[str, Any]],
                   keys: list[dict[str, Any]]) -> str:
    lines = [
        f"  - slug: {gateway['slug']}",
        f"    name: {gateway['name']!r}",
        f"    target_url: {gateway['target_url']}",
    ]
    if gateway.get("description"):
        lines.append(f"    description: {gateway['description']!r}")
    if gateway["auth_mode"] != "none":
        lines.append("    auth:")
        lines.append(f"      mode: {gateway['auth_mode']}")
        if gateway.get("idp"):
            lines.append(f"      idp: {gateway['idp']}")
        if gateway.get("client_id"):
            lines.append(f"      client_id: ${{CLIENT_ID_{gateway['slug'].upper()}}}")
        if gateway["auth_mode"] == "oauth":
            lines.append(
                f"      client_secret: ${{CLIENT_SECRET_{gateway['slug'].upper()}}}"
            )
            if gateway.get("config_url"):
                lines.append(f"      config_url: {gateway['config_url']}")
    if gateway.get("requests_per_minute"):
        lines.append(f"    rate_limit: {gateway['requests_per_minute']}")
    if principals:
        subjects = ", ".join(repr(p["subject"]) for p in principals)
        lines.append(f"    allow: [{subjects}]")
    if keys:
        labels = ", ".join(f"{k['label']!r}" for k in keys)
        lines.append(f"    api_keys: [{labels}]")
    return "\n".join(lines)


async def render_document(read_models: Any, tenant_id: str) -> str:
    """Every gateway, role and member in one tenant, as a document
    `apply_document` accepts.

    Members export without their password: an export is safe to commit, and the
    operator re-supplies it as `${VAR}` like every other secret. A member line
    therefore does not round-trip on its own — apply it with the password filled
    in, and the second apply is a no-op.
    """
    parts: list[str] = []

    entries: list[str] = []
    for gateway in await read_models.list_gateways(tenant_id):
        principals = await read_models.principals(gateway["id"])
        keys = await read_models.api_keys(gateway["id"])
        entries.append(render_gateway(gateway, principals, keys))
    parts.append("gateways:\n" + "\n".join(entries) if entries else "gateways: []")

    # `list_roles` already drops deleted rows; one query feeds both sections.
    roles = await read_models.list_roles(tenant_id)
    if roles:
        lines = ["roles:"]
        for role in sorted(roles, key=lambda r: r["name"]):
            perms = ", ".join(repr(p) for p in role["permissions"])
            lines.append(f"  - name: {role['name']!r}")
            lines.append(f"    permissions: [{perms}]")
        parts.append("\n".join(lines))

    operators = await read_models.list_operators(tenant_id)
    if operators:
        roles_by_id = {role_id: name for role_id, (name, _) in BUILTIN_ROLES.items()}
        roles_by_id.update({role["id"]: role["name"] for role in roles})
        lines = ["members:"]
        for operator in sorted(operators, key=lambda o: o["email"]):
            names = [roles_by_id.get(rid, rid) for rid in operator["role_ids"]]
            lines.append(f"  - email: {operator['email']}")
            if names:
                lines.append(f"    roles: [{', '.join(names)}]")
        parts.append("\n".join(lines))

    return "\n\n".join(parts) + "\n"
