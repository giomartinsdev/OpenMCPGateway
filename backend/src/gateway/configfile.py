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

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from gateway.domain.events import AuthMode, IdentityProvider
from gateway.domain.model import DomainError

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


@dataclass(frozen=True, slots=True)
class GatewaySpec:
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
    # Bearer the upstream MCP server demands; write-only, like client_secret.
    upstream_token: str | None = None
    rate_limit: int = 0
    allow: tuple[str, ...] = ()
    api_keys: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ConfigDocument:
    gateways: tuple[GatewaySpec, ...] = ()
    prune: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


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
        prune=bool(data.get("prune", False)),
        raw=loaded,
    )


def _parse_gateway(entry: dict[str, Any]) -> GatewaySpec:
    slug = str(entry["slug"]).strip()
    auth = entry.get("auth") or {}
    if auth:
        if not isinstance(auth, dict):
            raise ValueError("'auth' must be a mapping")
    mode = AuthMode(str(auth.get("mode", "none")))
    idp_value = auth.get("idp")
    idp = IdentityProvider(str(idp_value)) if idp_value else None
    scopes = tuple(str(scope) for scope in (auth.get("scopes") or ()))

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
        upstream_token=auth.get("upstream_token"),
        rate_limit=int(entry.get("rate_limit") or 0),
        allow=tuple(str(subject) for subject in (entry.get("allow") or ())),
        api_keys=tuple(str(label) for label in (entry.get("api_keys") or ())),
        enabled=bool(entry.get("enabled", True)),
    )


async def apply_document(document: ConfigDocument, commands: Any,
                         read_models: Any, *, actor: str = "config-file") -> dict:
    """Reconcile the declared gateways against current state.

    Returns a summary of what changed, which the CLI prints and the API returns
    so a failed sync is obvious instead of silent.
    """
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    issued_keys: list[dict[str, str]] = []

    existing = {gw["slug"]: gw for gw in await read_models.list_gateways()}
    declared = {spec.slug for spec in document.gateways}

    for spec in document.gateways:
        current = existing.get(spec.slug)
        try:
            if current is None:
                gateway_id = await commands.create_gateway(
                    slug=spec.slug,
                    name=spec.name,
                    target_url=spec.target_url,
                    description=spec.description,
                    auth_mode=spec.auth_mode,
                    idp=spec.idp,
                    actor=actor,
                )
                created.append(spec.slug)
                if spec.auth_mode is not AuthMode.NONE:
                    await commands.configure_auth(
                        gateway_id,
                        auth_mode=spec.auth_mode,
                        idp=spec.idp,
                        client_id=spec.client_id,
                        client_secret=spec.client_secret,
                        config_url=spec.config_url,
                        required_scopes=spec.required_scopes,
                        upstream_token=spec.upstream_token,
                        actor=actor,
                    )
                for label in spec.api_keys:
                    plaintext, key_id = await commands.issue_api_key(
                        gateway_id, label=label, actor=actor
                    )
                    issued_keys.append(
                        {"gateway": spec.slug, "label": label, "key": plaintext}
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
                if (current["auth_mode"] != spec.auth_mode.value
                        or (current["idp"] or None) != (spec.idp.value if spec.idp else None)):
                    await commands.configure_auth(
                        current["id"],
                        auth_mode=spec.auth_mode,
                        idp=spec.idp,
                        client_id=spec.client_id,
                        client_secret=spec.client_secret,
                        config_url=spec.config_url,
                        required_scopes=spec.required_scopes,
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

    return {
        "created": created,
        "updated": updated,
        "unchanged": skipped,
        "pruned": pruned,
        "errors": errors,
        "api_keys": issued_keys,
    }


__all__ = ["ConfigDocument", "ConfigFileError", "GatewaySpec", "apply_document",
           "parse_document"]
