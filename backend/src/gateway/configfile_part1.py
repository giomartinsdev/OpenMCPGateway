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
        api_keys:
          - label: ci
"""

import os
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from gateway.domain.events import AuthMode, IdentityProvider, VerifierSpec
from gateway.domain.model import SLUG_RE, DomainError
from gateway.domain.permissions import (
    BUILTIN_ROLES,
    DEFAULT_ROLE_IDS,
    PermissionCatalogError,
    expand,
    normalise,
)

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

#: Fields a `${VAR}` may appear in: credentials, never something echoed back.
_CREDENTIAL_FIELD_RE = re.compile(
    r"^(?:gateways\[\d+\]\.auth\.(?:client_id|client_secret|upstream_token)"
    r"|members\[\d+\]\.password)$"
)

class ConfigFileError(ValueError):
    """The YAML file is malformed or references an unset variable."""

def _expand(value: str) -> str:
    """Expand ${VAR} / ${VAR:-default} inside one credential field."""
    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        resolved = os.environ.get(name)
        if resolved is None:
            if default is None:
                raise ConfigFileError(f"environment variable {name} is not set")
            return default
        return resolved

    return _ENV_RE.sub(replace, value)

def _expand_document(value: Any, field: str = "") -> Any:
    """Expand a credential field, refuse `${VAR}` anywhere else.

    Expanding the whole document made `POST /api/config/validate` a way to read
    any variable the process holds: `slug` is echoed back after expansion and
    before its format is checked, so `${OMG_SESSION_SECRET}` came back as text.
    Refusing instead of leaving the text alone matters — an unevaluated
    placeholder would be stored and echoed as if it were the real value.
    """
    if isinstance(value, dict):
        return {
            key: _expand_document(item, f"{field}.{key}" if field else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _expand_document(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, str) and _ENV_RE.search(value):
        if not _CREDENTIAL_FIELD_RE.match(field):
            raise ConfigFileError(
                f"{field or 'document'}: environment expansion is only allowed in "
                "credential fields (auth.client_id, auth.client_secret, "
                "auth.upstream_token, members[].password)"
            )
        return _expand(value)
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

    data = _expand_document(loaded)
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
            name = str(entry["name"]).strip()
            # Checked here as well as in `decide_define`: `/config/validate`
            # echoes the name, so the format has to be right before it is
            # returned, not only before it is stored.
            if not name:
                raise ValueError("role name is required")
            if len(name) > 64:
                raise ValueError("role name must be 64 characters or fewer")
            spec = RoleSpec(
                name=name,
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
        if "@" not in email:
            raise ConfigFileError(f"members[{index}]: invalid email")
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
    # Same rule `create_gateway` enforces, applied at parse time: `/config/
    # validate` returns the slug, so it must already be a slug and not whatever
    # a placeholder expanded to. The message names the rule, not the value.
    if not SLUG_RE.match(slug):
        raise ValueError("slug must be lowercase alphanumeric with hyphens, 1-63 chars")
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
        api_keys=tuple(str(label) for label in (entry.get("api_keys") or ())),
        enabled=bool(entry.get("enabled", True)),
    )
