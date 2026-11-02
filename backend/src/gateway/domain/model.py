"""Aggregates: replay events into state, then decide new events from it.

Each aggregate is a pure reducer. `from_events` folds the stream; `decide_*`
methods validate a command against the folded state and return the events to
append. No I/O happens here — that is what makes the domain testable without a
database and keeps invariants in one place.

Invariants deliberately enforced here rather than in the API layer:
  * a slug is unique and URL-safe (it becomes a public path segment)
  * a gateway with OAuth auth must name an IdP and its client credentials
  * an API-key gateway must have at least one live key
  * rate limits are positive

The aggregates are Pydantic models so the same vocabulary is used on both sides
of the process boundary: an invalid slug is rejected by the same machinery that
would reject it in a request body.
"""

import re
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from gateway.domain.events import (
    SIGN_IN_AGGREGATE,
    ApiKeyIssued,
    ApiKeyRevoked,
    AuthMode,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayRateLimitSet,
    GatewayUpdated,
    IdentityProvider,
    OperatorRegistered,
    PrincipalAllowed,
    PrincipalRevoked,
    SignInConfigured,
)

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class DomainError(ValueError):
    """A command violated an invariant. Surfaced to the caller as HTTP 422."""


class _Aggregate(BaseModel):
    """Mutable state folded from events. Not frozen, not validated on assign.

    Validation happens when an event is constructed, so the state cannot become
    invalid without a bug in a reducer — hence no per-assignment validation,
    which would make folding a stream an order of magnitude slower for nothing.
    """

    model_config = ConfigDict(validate_assignment=False)


# --------------------------------------------------------------------------
# Operator
# --------------------------------------------------------------------------


class Operator(_Aggregate):
    id: str = ""
    email: str = ""
    password_hash: str = ""
    is_admin: bool = False
    exists: bool = False

    @classmethod
    def from_events(cls, aggregate_id: str, events: Iterable[Event]) -> Operator:
        state = cls(id=aggregate_id)
        for event in events:
            if isinstance(event, OperatorRegistered):
                state.email = event.email
                state.password_hash = event.password_hash
                state.is_admin = event.is_admin
                state.exists = True
        return state

    def decide_register(
        self, *, email: str, password_hash: str, is_admin: bool = False
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
            )
        ]


# --------------------------------------------------------------------------
# Gateway
# --------------------------------------------------------------------------


class Gateway(_Aggregate):
    id: str = ""
    slug: str = ""
    name: str = ""
    target_url: str = ""
    description: str = ""
    auth_mode: AuthMode = AuthMode.NONE
    idp: IdentityProvider | None = None
    client_id: str | None = None
    client_secret: str | None = None
    config_url: str | None = None
    required_scopes: tuple[str, ...] = ()
    upstream_token: str | None = None
    requests_per_minute: int = 0  # 0 = unlimited
    enabled: bool = True
    allowed_subjects: set[str] = Field(default_factory=set)
    api_key_ids: dict[str, str] = Field(default_factory=dict)  # key_id -> key_hash
    deleted: bool = False
    version: int = 0

    @classmethod
    def from_events(cls, aggregate_id: str, events: Iterable[Event]) -> Gateway:
        state = cls(id=aggregate_id)
        for event in events:
            state._apply(event)
            state.version = event.sequence
        return state

    def _apply(self, event: Event) -> None:
        match event:
            case GatewayCreated():
                self.slug = event.slug
                self.name = event.name
                self.target_url = event.target_url
                self.description = event.description
                self.auth_mode = event.auth_mode
                self.idp = event.idp
            case GatewayUpdated():
                if event.name is not None:
                    self.name = event.name
                if event.target_url is not None:
                    self.target_url = event.target_url
                if event.description is not None:
                    self.description = event.description
            case GatewayAuthConfigured():
                self.auth_mode = event.auth_mode
                self.idp = event.idp
                self.client_id = event.client_id
                self.client_secret = event.client_secret
                self.config_url = event.config_url
                self.required_scopes = event.required_scopes
                self.upstream_token = event.upstream_token
            case GatewayRateLimitSet():
                self.requests_per_minute = event.requests_per_minute
            case GatewayDisabled():
                self.enabled = False
            case GatewayEnabled():
                self.enabled = True
            case PrincipalAllowed():
                self.allowed_subjects.add(event.subject)
            case PrincipalRevoked():
                self.allowed_subjects.discard(event.subject)
            case ApiKeyIssued():
                self.api_key_ids[event.key_id] = event.key_hash
            case ApiKeyRevoked():
                self.api_key_ids.pop(event.key_id, None)
            case GatewayDeleted():
                self.deleted = True
                self.enabled = False

    # -- invariants -------------------------------------------------------

    def _ensure_live(self) -> None:
        if self.deleted:
            raise DomainError("gateway was deleted")
        if not self.slug:
            raise DomainError("gateway does not exist")

    def _validate_auth(
        self,
        mode: AuthMode,
        idp: IdentityProvider | None,
        client_id: str | None,
        client_secret: str | None,
        config_url: str | None,
    ) -> None:
        if mode is AuthMode.OAUTH:
            if idp is None:
                raise DomainError("oauth auth requires an identity provider")
            if not client_id:
                raise DomainError("oauth auth requires client_id")
            if idp is IdentityProvider.OIDC and not config_url:
                raise DomainError("oidc auth requires config_url")
            if idp is not IdentityProvider.OIDC and not client_secret:
                raise DomainError("oauth auth requires client_secret")

    # -- commands ---------------------------------------------------------

    def decide_create(
        self,
        *,
        slug: str,
        name: str,
        target_url: str,
        description: str = "",
        auth_mode: AuthMode = AuthMode.NONE,
        idp: IdentityProvider | None = None,
    ) -> list[Event]:
        if self.slug:
            raise DomainError("gateway already exists")
        if not SLUG_RE.match(slug):
            raise DomainError(
                "slug must be lowercase alphanumeric with hyphens, 1-63 chars"
            )
        if not name.strip():
            raise DomainError("name is required")
        if not target_url.startswith(("http://", "https://")):
            raise DomainError("target_url must be an http(s) URL")
        if auth_mode is AuthMode.OAUTH and idp is None:
            raise DomainError("oauth auth requires an identity provider")
        return [
            GatewayCreated(
                aggregate_id=self.id,
                slug=slug,
                name=name.strip(),
                target_url=target_url,
                description=description,
                auth_mode=auth_mode,
                idp=idp,
            )
        ]

    def decide_update(
        self,
        *,
        name: str | None = None,
        target_url: str | None = None,
        description: str | None = None,
    ) -> list[Event]:
        self._ensure_live()
        if target_url is not None and not target_url.startswith(
            ("http://", "https://")
        ):
            raise DomainError("target_url must be an http(s) URL")
        if name is not None and not name.strip():
            raise DomainError("name cannot be empty")
        if name is None and target_url is None and description is None:
            raise DomainError("nothing to update")
        return [
            GatewayUpdated(
                aggregate_id=self.id,
                name=name.strip() if name else None,
                target_url=target_url,
                description=description,
            )
        ]

    def decide_configure_auth(
        self,
        *,
        auth_mode: AuthMode,
        idp: IdentityProvider | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        config_url: str | None = None,
        required_scopes: Sequence[str] = (),
        upstream_token: str | None = None,
    ) -> list[Event]:
        self._ensure_live()
        # Keep previously stored secret when the caller only rotates the mode.
        if auth_mode is AuthMode.OAUTH and client_secret is None and self.client_secret:
            client_secret = self.client_secret
        if auth_mode is AuthMode.OAUTH and client_id is None and self.client_id:
            client_id = self.client_id
        if upstream_token is None:
            upstream_token = self.upstream_token
        self._validate_auth(auth_mode, idp, client_id, client_secret, config_url)
        # Switching *to* API keys needs a key to exist, because otherwise the route
        # would flip from protected to "accepts nothing" and look broken. Keeping
        # the mode you already have must not demand a new key: that blocks
        # unrelated edits such as rotating the upstream token.
        switching_to_keys = auth_mode is AuthMode.API_KEY and (
            self.auth_mode is not AuthMode.API_KEY
        )
        if switching_to_keys and not self.api_key_ids:
            raise DomainError(
                "api_key auth requires at least one issued key; issue a key first"
            )
        return [
            GatewayAuthConfigured(
                aggregate_id=self.id,
                auth_mode=auth_mode,
                idp=idp,
                client_id=client_id,
                client_secret=client_secret,
                config_url=config_url,
                required_scopes=tuple(required_scopes),
                upstream_token=upstream_token,
            )
        ]

    def decide_set_rate_limit(self, *, requests_per_minute: int) -> list[Event]:
        self._ensure_live()
        if requests_per_minute < 0:
            raise DomainError("requests_per_minute cannot be negative")
        return [
            GatewayRateLimitSet(
                aggregate_id=self.id, requests_per_minute=requests_per_minute
            )
        ]

    def decide_allow(self, *, subject: str, note: str = "") -> list[Event]:
        self._ensure_live()
        subject = subject.strip()
        if not subject:
            raise DomainError("subject is required")
        if subject in self.allowed_subjects:
            raise DomainError(f"'{subject}' is already allowed")
        return [PrincipalAllowed(aggregate_id=self.id, subject=subject, note=note)]

    def decide_revoke(self, *, subject: str) -> list[Event]:
        self._ensure_live()
        if subject not in self.allowed_subjects:
            raise DomainError(f"'{subject}' is not allowed")
        return [PrincipalRevoked(aggregate_id=self.id, subject=subject)]

    def decide_disable(self, *, reason: str = "") -> list[Event]:
        self._ensure_live()
        if not self.enabled:
            raise DomainError("gateway is already disabled")
        return [GatewayDisabled(aggregate_id=self.id, reason=reason)]

    def decide_enable(self) -> list[Event]:
        self._ensure_live()
        if self.enabled:
            raise DomainError("gateway is already enabled")
        return [GatewayEnabled(aggregate_id=self.id)]

    def decide_delete(self) -> list[Event]:
        self._ensure_live()
        return [GatewayDeleted(aggregate_id=self.id)]

    def decide_issue_key(
        self, *, key_id: str, label: str, key_hash: str, subject: str = "api-key"
    ) -> list[Event]:
        self._ensure_live()
        if not label.strip():
            raise DomainError("label is required")
        if key_id in self.api_key_ids:
            raise DomainError("key already exists")
        return [
            ApiKeyIssued(
                aggregate_id=self.id,
                key_id=key_id,
                label=label.strip(),
                key_hash=key_hash,
                subject=subject,
            )
        ]

    def decide_revoke_key(self, *, key_id: str) -> list[Event]:
        self._ensure_live()
        if key_id not in self.api_key_ids:
            raise DomainError("key not found")
        return [ApiKeyRevoked(aggregate_id=self.id, key_id=key_id)]

    # -- read-side helpers ------------------------------------------------

    def authorizes(self, subject: str | None) -> bool:
        """True when this gateway lets `subject` through."""
        if not self.enabled or self.deleted:
            return False
        if self.auth_mode is AuthMode.NONE:
            return True
        if not subject:
            return False
        # No allowlist configured means "any authenticated principal".
        return (
            not self.allowed_subjects
            or subject in self.allowed_subjects
            or "*" in self.allowed_subjects
        )

    def matches_key_hash(self, key_hash: str) -> str | None:
        """Return the subject for a matching key hash, if any."""
        for key_id, stored in self.api_key_ids.items():
            if stored == key_hash:
                return f"api-key:{key_id}"
        return None


# --------------------------------------------------------------------------
# Sign-in settings
# --------------------------------------------------------------------------


class SignInSettings(_Aggregate):
    """How an operator logs in to the panel — one per deployment."""

    id: str = SIGN_IN_AGGREGATE
    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_uri: str = ""
    operator_allowlist: tuple[str, ...] = ()
    version: int = 0

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> SignInSettings:
        settings = cls()
        for event in events:
            settings._apply(event)
            settings.version = event.sequence
        return settings

    def _apply(self, event: Event) -> None:
        match event:
            case SignInConfigured():
                self.google_client_id = event.google_client_id
                self.google_client_secret = event.google_client_secret
                self.redirect_uri = event.redirect_uri
                self.operator_allowlist = tuple(event.operator_allowlist)

    @property
    def enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def decide_configure(
        self,
        *,
        google_client_id: str,
        google_client_secret: str,
        redirect_uri: str,
        operator_allowlist: Sequence[str] = (),
    ) -> list[Event]:
        """Validate, keeping the stored secret when the caller omits it.

        The panel never receives the secret back, so it cannot send it again. An
        empty secret therefore means "unchanged", not "clear it" — otherwise every
        edit that touches anything else silently breaks sign-in.
        """
        client_id = google_client_id.strip()
        redirect = redirect_uri.strip()
        secret = google_client_secret or self.google_client_secret

        if not client_id:
            raise DomainError("google_client_id is required")
        if not secret:
            raise DomainError("google_client_secret is required")
        if not redirect:
            # Naming the field beats "invalid configuration": the failure this
            # prevents shows up at the provider, not here.
            raise DomainError(
                "redirect_uri is required — it must match the authorised redirect "
                "URI registered at the provider exactly"
            )
        if not redirect.startswith(("http://", "https://")):
            raise DomainError("redirect_uri must be an absolute http(s) URL")

        allowlist = tuple(
            email.strip().lower() for email in operator_allowlist if email.strip()
        )
        for email in allowlist:
            if "@" not in email:
                raise DomainError(f"'{email}' is not an email address")

        return [
            SignInConfigured(
                aggregate_id=self.id,
                google_client_id=client_id,
                google_client_secret=secret,
                redirect_uri=redirect,
                operator_allowlist=allowlist,
            )
        ]


__all__ = [
    "SLUG_RE",
    "DomainError",
    "Gateway",
    "Operator",
    "SignInSettings",
]
