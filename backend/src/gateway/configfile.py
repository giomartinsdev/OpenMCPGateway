"""Declarative gateway definitions (GitOps).

The UI and this file are two front-ends over the same commands — neither is
privileged. A YAML file is applied as a batch of commands, so imports are
auditable events like any other mutation.

Merge semantics: YAML is authoritative for what it declares. A gateway absent
from the file is left alone (a typo in the file must not delete production); set
`prune: true` to opt into deletion.

    gateways:
      - slug: rancher
        name: Rancher cluster
        target_url: http://rancher-mcp.support.svc.cluster.local:8000/mcp
        auth:
          mode: oauth
          idp: github
          client_id: ${GITHUB_CLIENT_ID}
          client_secret: ${GITHUB_CLIENT_SECRET}
          # Only when the upstream server has its own token (e.g. rancher-mcp).
          upstream_token: ${RANCHER_MCP_TOKEN}
        rate_limit: 120
        allow: [giomartinsdev, "*"]
        api_keys:
          - label: ci
"""

import os
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from gateway.domain.events import AuthMode, IdentityProvider, VerifierSpec
from gateway.domain.model import DomainError
from gateway.domain.permissions import (
    BUILTIN_ROLES,
    DEFAULT_ROLE_IDS,
    PermissionCatalogError,
    expand,
    normalise,
)

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigFileError(ValueError):
    """The YAML file is malformed or references an unset variable."""


def _expand(value: Any) -> Any:
    """Expand ${VAR} / ${VAR:-default} recursively."""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is None:
                if default is None:
                    raise ConfigFileError(f"environment variable {name} is not set")
                return default
            return resolved

        return _ENV_RE.sub(replace, value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


class GatewaySpec(BaseModel):
    """One gateway as declared in the file. Validated on construction."""

    # Declared specs cross a process boundary (YAML in, events out); validating
    # them here means a typo'd mode fails at parse time with the entry named.
    model_config = ConfigDict(frozen=True)

    slug: str
    name: str
    target_url: str
    description: str = ""
    auth_mode: AuthMode = AuthMode.NONE
    idp: IdentityProvider | None = None
    client_id: str | None = None
    client_secret: str | None = None
    config_url: str | None = None
    required_scopes: tuple[str, ...] = ()
    #: Accept keys as well as the login above (`auth.allow_api_key`).
    allow_api_key: bool = False
    #: Extra issuers verified without hosting their login (`auth.verifiers`).
    verifiers: tuple[VerifierSpec, ...] = ()
    # Bearer the upstream MCP server demands; write-only, like client_secret.
    upstream_token: str | None = None
    rate_limit: int = 0
    allow: tuple[str, ...] = ()
    api_keys: tuple[str, ...] = ()
    enabled: bool = True


class RoleSpec(BaseModel):
    """A custom role. Built-ins are code; the file may name them, not change them."""

    model_config = ConfigDict(frozen=True)

    name: str
    permissions: tuple[str, ...] = ()

class MemberSpec(BaseModel):
    """An account in the tenant, by email, plus the roles it should hold.

    `password` is only used when the account does not exist yet, and is expected
    to be a `${ENV_VAR}` placeholder rather than a literal: a file with a
    plaintext password is a credential in git. An exported file therefore has no
    password at all, and applying it against the same tenant is a no-op — which
    is what makes the export a round trip instead of a dead end.
    """

    model_config = ConfigDict(frozen=True)

    email: str
    password: str = ""
    roles: tuple[str, ...] = ()

class ConfigDocument(BaseModel):
    """A parsed file. `raw` is kept for echoing back what was read."""

    model_config = ConfigDict(frozen=True)

    gateways: tuple[GatewaySpec, ...] = ()
    roles: tuple[RoleSpec, ...] = ()
    members: tuple[MemberSpec, ...] = ()
    prune: bool = False
    raw: dict[str, Any] = {}


def parse_document(text: str) -> ConfigDocument:
    try:
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigFileError("top level must be a mapping")

    data = _expand(loaded)
    entries = data.get("gateways") or []
    if not isinstance(entries, list):
        raise ConfigFileError("'gateways' must be a list")

    specs: list[GatewaySpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigFileError(f"gateways[{index}] must be a mapping")
        try:
            spec = _parse_gateway(entry)
        except (KeyError, ValueError) as exc:
            raise ConfigFileError(f"gateways[{index}]: {exc}") from exc
        if spec.slug in seen:
            raise ConfigFileError(f"duplicate slug '{spec.slug}'")
        seen.add(spec.slug)
        specs.append(spec)

    return ConfigDocument(
        gateways=tuple(specs),
        roles=tuple(_parse_roles(data.get("roles") or [])),
        members=tuple(_parse_members(data.get("members") or [])),
        prune=bool(data.get("prune", False)),
        raw=loaded,
    )

def _entries(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ConfigFileError(f"'{key}' must be a list")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ConfigFileError(f"{key}[{index}] must be a mapping")
    return value

def _parse_roles(raw: Any) -> list[RoleSpec]:
    specs: list[RoleSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(_entries(raw, "roles")):
        try:
            spec = RoleSpec(
                name=str(entry["name"]).strip(),
                permissions=tuple(str(p) for p in (entry.get("permissions") or ())),
            )
        except (KeyError, ValueError) as exc:
            raise ConfigFileError(f"roles[{index}]: {exc}") from exc
        if spec.name in seen:
            raise ConfigFileError(f"duplicate role '{spec.name}'")
        seen.add(spec.name)
        specs.append(spec)
    return specs

def _parse_members(raw: Any) -> list[MemberSpec]:
    specs: list[MemberSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(_entries(raw, "members")):
        email = str(entry.get("email") or "").strip().lower()
        if not email:
            raise ConfigFileError(f"members[{index}]: email is required")
        if email in seen:
            raise ConfigFileError(f"duplicate member '{email}'")
        seen.add(email)
        specs.append(
            MemberSpec(
                email=email,
                password=str(entry.get("password") or ""),
                roles=tuple(str(r) for r in (entry.get("roles") or ())),
            )
        )
    return specs


def _parse_gateway(entry: dict[str, Any]) -> GatewaySpec:
    slug = str(entry["slug"]).strip()
    auth = entry.get("auth") or {}
    if auth and not isinstance(auth, dict):
        raise ValueError("'auth' must be a mapping")
    mode = AuthMode(str(auth.get("mode", "none")))
    idp_value = auth.get("idp")
    idp = IdentityProvider(str(idp_value)) if idp_value else None
    scopes = tuple(str(scope) for scope in (auth.get("scopes") or ()))
    verifiers = tuple(
        VerifierSpec(
            label=str(entry["label"]),
            issuer=str(entry["issuer"]),
            audience=str(entry.get("audience") or ""),
            jwks_uri=str(entry["jwks_uri"]),
        )
        for entry in (auth.get("verifiers") or ())
    )

    return GatewaySpec(
        slug=slug,
        name=str(entry.get("name") or slug),
        target_url=str(entry["target_url"]),
        description=str(entry.get("description") or ""),
        auth_mode=mode,
        idp=idp,
        client_id=auth.get("client_id"),
        client_secret=auth.get("client_secret"),
        config_url=auth.get("config_url"),
        required_scopes=scopes,
        allow_api_key=bool(auth.get("allow_api_key", False)),
        verifiers=verifiers,
        upstream_token=auth.get("upstream_token"),
        rate_limit=int(entry.get("rate_limit") or 0),
        allow=tuple(str(subject) for subject in (entry.get("allow") or ())),
        api_keys=tuple(str(label) for label in (entry.get("api_keys") or ())),
        enabled=bool(entry.get("enabled", True)),
    )


def _auth_changed(current: dict[str, Any], spec: GatewaySpec) -> bool:
    """True when the declared auth block differs from what the gateway holds.

    Credentials are excluded on purpose: the file writes `${ENV}` placeholders
    that resolve to the same secret every apply, and rotating one is an event
    nobody asked for on a no-op sync.
    """
    if current["auth_mode"] != spec.auth_mode.value:
        return True
    if (current["idp"] or None) != (spec.idp.value if spec.idp else None):
        return True
    if bool(current["allow_api_key"]) != spec.allow_api_key:
        return True
    stored = [
        (v["label"], v["issuer"], v.get("audience") or "", v["jwks_uri"])
        for v in current["verifiers"]
    ]
    declared = [
        (v.label, v.issuer, v.audience, v.jwks_uri) for v in spec.verifiers
    ]
    return stored != declared

async def plan_document(document: ConfigDocument, read_models: Any, *,
                        tenant_id: str) -> dict[str, Any]:
    """What an apply would do, without doing it.

    `prune: true` plus a truncated file is a delete nobody saw coming. The plan
    is the diff that makes it visible before it happens — the same merge rules as
    `apply_document`, so a plan cannot disagree with the apply it describes.

    It reads only: no command handler is touched, which is why this is safe to
    call from a screen that has not been confirmed.
    """
    existing = {
        row["slug"]: row for row in await read_models.list_gateways(tenant_id)
    }
    declared = {spec.slug for spec in document.gateways}

    create: list[str] = []
    update: list[dict[str, Any]] = []
    unchanged: list[str] = []
    errors: list[str] = []

    for spec in document.gateways:
        current = existing.get(spec.slug)
        if current is None:
            create.append(spec.slug)
            continue
        changes: list[str] = []
        if current["name"] != spec.name:
            changes.append("name")
        if current["target_url"] != spec.target_url:
            changes.append("target_url")
        if (current["description"] or "") != spec.description:
            changes.append("description")
        if _auth_changed(current, spec):
            changes.append("auth")
        if int(current["requests_per_minute"]) != spec.rate_limit:
            changes.append("rate_limit")
        if changes:
            update.append({"slug": spec.slug, "fields": changes})
        else:
            unchanged.append(spec.slug)

    # Pruning is the destructive half, so it is reported even when the file did
    # not ask for it: an empty `delete` is the reassuring answer.
    delete = (
        sorted(slug for slug in existing if slug not in declared)
        if document.prune
        else []
    )

    return {
        "create": create,
        "update": update,
        "delete": delete,
        "unchanged": unchanged,
        "errors": errors,
        "prune": document.prune,
    }


async def apply_document(document: ConfigDocument, commands: Any,
                         read_models: Any, *, tenant_id: str,
                         actor: str = "config-file",
                         caller: Any = None) -> dict:
    """Reconcile the file against current state, inside one tenant.

    Gateways, roles and members are all reconciled here, so a tenant can be
    rebuilt from a commit. `caller` is the account applying the file: when it is
    given, the delegation rule applies (nobody grants a permission it does not
    hold itself) — the same rule the panel enforces, because a file must not be
    a way around a button.

    Returns a summary of what changed, which the CLI prints and the API returns
    so a failed sync is obvious instead of silent.
    """
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    issued_keys: list[dict[str, str]] = []

    existing = {
        gw["slug"]: gw for gw in await read_models.list_gateways(tenant_id)
    }
    declared = {spec.slug for spec in document.gateways}

    for spec in document.gateways:
        current = existing.get(spec.slug)
        try:
            if current is None:
                gateway_id = await commands.create_gateway(
                    tenant_id=tenant_id,
                    slug=spec.slug,
                    name=spec.name,
                    target_url=spec.target_url,
                    description=spec.description,
                    auth_mode=spec.auth_mode,
                    idp=spec.idp,
                    actor=actor,
                )
                created.append(spec.slug)
                # Keys before auth: a gateway that accepts keys has to have one
                # to accept, and the domain refuses the combination otherwise.
                for label in spec.api_keys:
                    plaintext, _key_id = await commands.issue_api_key(
                        gateway_id, label=label, actor=actor
                    )
                    issued_keys.append(
                        {"gateway": spec.slug, "label": label, "key": plaintext}
                    )
                if spec.auth_mode is not AuthMode.NONE:
                    await commands.configure_auth(
                        gateway_id,
                        auth_mode=spec.auth_mode,
                        idp=spec.idp,
                        client_id=spec.client_id,
                        client_secret=spec.client_secret,
                        config_url=spec.config_url,
                        required_scopes=spec.required_scopes,
                        allow_api_key=spec.allow_api_key,
                        verifiers=spec.verifiers,
                        upstream_token=spec.upstream_token,
                        actor=actor,
                    )
                for subject in spec.allow:
                    await commands.allow_principal(
                        gateway_id, subject=subject, actor=actor
                    )
                if spec.rate_limit:
                    await commands.set_rate_limit(
                        gateway_id, requests_per_minute=spec.rate_limit, actor=actor
                    )
                if not spec.enabled:
                    await commands.disable_gateway(
                        gateway_id, reason="disabled in config file", actor=actor
                    )
            else:
                changed = []
                if current["target_url"] != spec.target_url:
                    changed.append("target_url")
                if current["name"] != spec.name:
                    changed.append("name")
                if current["description"] != spec.description:
                    changed.append("description")
                if changed:
                    await commands.update_gateway(
                        current["id"],
                        name=spec.name if "name" in changed else None,
                        target_url=spec.target_url if "target_url" in changed else None,
                        description=(spec.description
                                     if "description" in changed else None),
                        actor=actor,
                    )
                if (_auth_changed(current, spec)):
                    await commands.configure_auth(
                        current["id"],
                        auth_mode=spec.auth_mode,
                        idp=spec.idp,
                        client_id=spec.client_id,
                        client_secret=spec.client_secret,
                        config_url=spec.config_url,
                        required_scopes=spec.required_scopes,
                        allow_api_key=spec.allow_api_key,
                        verifiers=spec.verifiers,
                        upstream_token=spec.upstream_token,
                        actor=actor,
                    )
                    changed.append("auth")
                if int(current["requests_per_minute"]) != spec.rate_limit:
                    await commands.set_rate_limit(
                        current["id"], requests_per_minute=spec.rate_limit, actor=actor
                    )
                    changed.append("rate_limit")

                current_subjects = {
                    p["subject"] for p in await read_models.principals(current["id"])
                }
                for subject in set(spec.allow) - current_subjects:
                    await commands.allow_principal(
                        current["id"], subject=subject, actor=actor
                    )
                    changed.append(f"allow:{subject}")
                for subject in current_subjects - set(spec.allow):
                    await commands.revoke_principal(
                        current["id"], subject=subject, actor=actor
                    )
                    changed.append(f"revoke:{subject}")

                if changed:
                    updated.append(spec.slug)
                else:
                    skipped.append(spec.slug)
        except DomainError as exc:
            errors.append(f"{spec.slug}: {exc}")

    pruned: list[str] = []
    if document.prune:
        for slug, current in existing.items():
            if slug not in declared:
                await commands.delete_gateway(current["id"], actor=actor)
                pruned.append(slug)

    role_result = {"created": [], "updated": [], "unchanged": []}
    if document.roles:
        role_result = await _apply_roles(
            document.roles, commands, read_models, tenant_id=tenant_id,
            actor=actor, permissions=caller.permissions if caller else None,
        )
        errors.extend(role_result["errors"])

    member_result = {"created": [], "updated": [], "unchanged": []}
    if document.members:
        member_result = await _apply_members(
            document.members, commands, read_models, tenant_id=tenant_id,
            actor=actor, permissions=caller.permissions if caller else None,
        )
        errors.extend(member_result["errors"])

    return {
        "created": created,
        "updated": updated,
        "unchanged": skipped,
        "pruned": pruned,
        "errors": errors,
        "api_keys": issued_keys,
        "roles": role_result,
        "members": member_result,
    }

async def _role_table(read_models: Any, tenant_id: str) -> dict[str, dict[str, Any]]:
    """Role name -> {id, permissions, builtin}, for one tenant.

    Names, not ids: a file a human reviews says `Auditor`. Built-ins come first
    from code, then the tenant's own from the log — same order as the panel.
    """
    table = {
        name: {"id": role_id, "permissions": tuple(perms), "builtin": True}
        for role_id, (name, perms) in BUILTIN_ROLES.items()
    }
    for row in await read_models.list_roles(tenant_id):
        table[row["name"]] = {
            "id": row["id"],
            "permissions": tuple(row["permissions"] or ()),
            "builtin": False,
        }
    return table

async def _apply_roles(specs: Any, commands: Any, read_models: Any, *, tenant_id: str,
                       actor: str, permissions: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"created": [], "updated": [], "unchanged": [],
                              "errors": [], "table": {}}
    table = await _role_table(read_models, tenant_id)
    for spec in specs:
        try:
            wanted = normalise(spec.permissions)
        except PermissionCatalogError as exc:
            result["errors"].append(f"role {spec.name}: {exc}")
            continue
        if permissions is not None and not set(wanted) <= set(permissions):
            beyond = ", ".join(sorted(set(wanted) - set(permissions)))
            result["errors"].append(
                f"role {spec.name}: you cannot grant permission(s) you do not hold: {beyond}"
            )
            continue
        current = table.get(spec.name)
        if current is not None and current["builtin"]:
            if current["permissions"] != wanted:
                result["errors"].append(
                    f"role {spec.name}: built-in roles cannot be redefined"
                )
            else:
                result["unchanged"].append(spec.name)
            result["table"][spec.name] = current
            continue
        if current is None:
            role_id = await commands.define_role(
                tenant_id=tenant_id, name=spec.name, permissions=wanted, actor=actor
            )
            result["created"].append(spec.name)
            result["table"][spec.name] = {
                "id": role_id, "permissions": wanted, "builtin": False,
            }
        elif tuple(current["permissions"]) != wanted:
            await commands.define_role(
                tenant_id=tenant_id, role_id=current["id"], name=spec.name,
                permissions=wanted, actor=actor,
            )
            result["updated"].append(spec.name)
            result["table"][spec.name] = {**current, "permissions": wanted}
        else:
            result["unchanged"].append(spec.name)
            result["table"][spec.name] = current
    return result

async def _apply_members(specs: Any, commands: Any, read_models: Any, *, tenant_id: str,
                         actor: str, permissions: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"created": [], "updated": [], "unchanged": [],
                              "errors": []}
    table = await _role_table(read_models, tenant_id)
    existing = {
        row["email"]: row for row in await read_models.list_operators(tenant_id)
    }
    for spec in specs:
        unknown = [name for name in spec.roles if name not in table]
        if unknown:
            result["errors"].append(
                f"member {spec.email}: unknown role(s): {', '.join(unknown)}"
            )
            continue
        wanted = tuple(table[name]["id"] for name in spec.roles) or DEFAULT_ROLE_IDS
        granted = expand(wanted, {info["id"]: info["permissions"] for info in table.values()})
        if permissions is not None and not set(granted) <= set(permissions):
            beyond = ", ".join(sorted(set(granted) - set(permissions)))
            result["errors"].append(
                f"member {spec.email}: you cannot grant permission(s) you do not hold: "
                f"{beyond}"
            )
            continue
        current = existing.get(spec.email)
        if current is None:
            if not spec.password:
                result["errors"].append(
                    f"member {spec.email}: password is required to create the account"
                )
                continue
            try:
                await commands.register_operator(
                    email=spec.email, password=spec.password, tenant_id=tenant_id,
                    role_ids=wanted,
                )
            except DomainError as exc:
                result["errors"].append(f"member {spec.email}: {exc}")
                continue
            result["created"].append(spec.email)
        elif tuple(current["role_ids"]) != tuple(wanted):
            await commands.assign_roles(current["operator_id"], role_ids=wanted,
                                        actor=actor)
            result["updated"].append(spec.email)
        else:
            result["unchanged"].append(spec.email)
    return result


__all__ = ["ConfigDocument", "ConfigFileError", "GatewaySpec", "MemberSpec",
           "RoleSpec", "apply_document", "parse_document"]
