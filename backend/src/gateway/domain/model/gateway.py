"""A proxied MCP server, and every rule about its slug, auth and rate limit.

Invariants deliberately enforced here rather than in the API layer:
  * a slug is unique and URL-safe (it becomes a public path segment)
  * a gateway with OAuth auth must name an IdP and its client credentials
  * an API-key gateway must have at least one live key
  * rate limits are positive
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from pydantic import Field

from gateway.domain.events import (
    TENANT_OF_DEPLOYMENT,
    ApiKeyIssued,
    ApiKeyRevealed,
    ApiKeyRevoked,
    AuthMode,
    Event,
    GatewayAuthConfigured,
    GatewayCreated,
    GatewayDeleted,
    GatewayDisabled,
    GatewayEnabled,
    GatewayQuotaSet,
    GatewayRateLimitSet,
    GatewayRestored,
    GatewayToolsConfigured,
    GatewayUpdated,
    IdentityProvider,
    PrincipalAllowed,
    PrincipalRevoked,
    WebhookDeleted,
    WebhookRegistered,
)
from gateway.domain.model.base import SLUG_RE, DomainError, _Aggregate


class Gateway(_Aggregate):
    id: str = ""
    #: Which tenant owns this gateway. Empty on streams written before tenancy;
    #: folded from the event so a gateway created before the change still reads
    #: as the legacy tenant's (see `from_events`).
    tenant_id: str = TENANT_OF_DEPLOYMENT
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
    #: Calls this gateway may serve per calendar month. 0 = unlimited.
    monthly_quota: int = 0
    enabled: bool = True
    allowed_subjects: set[str] = Field(default_factory=set)
    api_key_ids: dict[str, str] = Field(default_factory=dict)  # key_id -> key_hash
    #: Tools the upstream may expose through this gateway. Empty means every
    #: one of them, which is what every gateway did before this existed.
    tools: tuple[str, ...] = ()
    #: Hook id -> URL. The secret is in the event, sealed in the read model.
    webhooks: dict[str, str] = Field(default_factory=dict)
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
                self.tenant_id = event.tenant_id
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
            case GatewayQuotaSet():
                self.monthly_quota = event.monthly_quota
            case GatewayRestored():
                # The whole configuration, not a diff: a rollback is one fact.
                # Credentials and keys are deliberately absent from the event, so
                # a restore cannot resurrect a revoked key.
                self.name = event.name
                self.target_url = event.target_url
                self.description = event.description
                self.requests_per_minute = event.requests_per_minute
                self.tools = tuple(event.tools)
            case GatewayDisabled():
                self.enabled = False
            case GatewayEnabled():
                self.enabled = True
            case PrincipalAllowed():
                self.allowed_subjects.add(event.subject)
            case PrincipalRevoked():
                self.allowed_subjects.discard(event.subject)
            case GatewayToolsConfigured():
                self.tools = tuple(event.tools)
            case ApiKeyIssued():
                self.api_key_ids[event.key_id] = event.key_hash
            case ApiKeyRevoked():
                self.api_key_ids.pop(event.key_id, None)
            case WebhookRegistered():
                self.webhooks[event.webhook_id] = event.url
            case WebhookDeleted():
                self.webhooks.pop(event.webhook_id, None)
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
        tenant_id: str,
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
                tenant_id=tenant_id,
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

    def decide_set_quota(self, *, monthly_quota: int) -> list[Event]:
        self._ensure_live()
        if monthly_quota < 0:
            raise DomainError("monthly_quota cannot be negative")
        return [GatewayQuotaSet(aggregate_id=self.id, monthly_quota=monthly_quota)]

    def decide_restore(self, *, version: int, previous: Gateway) -> list[Event]:
        """Roll the configuration back to what `previous` held.

        Credentials, keys and the enable flag are not restored: a revoked key
        must stay revoked, a rotated secret is not recoverable from the event
        payload, and "restore the disabled state" reads as an outage request.
        The caller names the version it asked for so the audit line is explicit.
        """
        self._ensure_live()
        if previous.version != version:
            raise DomainError(f"no such version: {version}")
        target = previous
        if (target.name, target.target_url, target.description,
                target.requests_per_minute, tuple(target.tools)) == (
                self.name, self.target_url, self.description,
                self.requests_per_minute, tuple(self.tools)):
            # A no-op restore that writes an event fills the log with "nothing
            # happened", which costs a reader attention every time they audit.
            raise DomainError("nothing to restore: already at that configuration")
        return [
            GatewayRestored(
                aggregate_id=self.id,
                restored_from=version,
                name=target.name,
                target_url=target.target_url,
                description=target.description,
                requests_per_minute=target.requests_per_minute,
                tools=tuple(target.tools),
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

    def decide_set_tools(self, *, tools: Sequence[str]) -> list[Event]:
        """Allowlist of upstream tool names. Empty means "all of them".

        Names are not validated against the upstream here: the write side has no
        network and the upstream may be down. A name that does not exist simply
        matches nothing, and the panel shows the upstream's real list next to the
        configured one so the difference is visible rather than guessed at.
        """
        self._ensure_live()
        cleaned = tuple(dict.fromkeys(t.strip() for t in tools if t.strip()))
        if len(cleaned) > 256:
            raise DomainError("at most 256 tools can be allowed")
        for name in cleaned:
            if len(name) > 128:
                raise DomainError("a tool name must be 128 characters or fewer")
        return [GatewayToolsConfigured(aggregate_id=self.id, tools=cleaned)]

    def decide_register_webhook(
        self, *, webhook_id: str, url: str, secret: str,
        outcomes: Sequence[str] = (),
    ) -> list[Event]:
        """A hook needs a URL a POST can reach and a secret a receiver can check.

        `file://` is refused here rather than at delivery: a hook that fails on
        every attempt because its scheme was never deliverable is a hook that
        looks broken instead of misconfigured.
        """
        self._ensure_live()
        if not url.startswith(("http://", "https://")):
            raise DomainError("webhook url must be an http(s) URL")
        if not secret.strip():
            raise DomainError(
                "webhook secret is required: an unsigned POST cannot be trusted"
            )
        if webhook_id in self.webhooks:
            raise DomainError("webhook already exists")
        cleaned = tuple(dict.fromkeys(o.strip() for o in outcomes if o.strip()))
        return [
            WebhookRegistered(
                aggregate_id=self.id,
                webhook_id=webhook_id,
                url=url,
                secret=secret,
                outcomes=cleaned,
            )
        ]

    def decide_delete_webhook(self, *, webhook_id: str) -> list[Event]:
        self._ensure_live()
        if webhook_id not in self.webhooks:
            raise DomainError("no such webhook")
        return [WebhookDeleted(aggregate_id=self.id, webhook_id=webhook_id)]

    def decide_issue_key(
        self,
        *,
        key_id: str,
        label: str,
        key_hash: str,
        subject: str = "api-key",
        sealed: str = "",
        expires_at: datetime | None = None,
    ) -> list[Event]:
        self._ensure_live()
        if not label.strip():
            raise DomainError("label is required")
        if key_id in self.api_key_ids:
            raise DomainError("key already exists")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            # A key minted already dead is a mistake in the caller, not a
            # deliberate revocation; refusing here names the field instead of
            # handing back a credential that answers 401.
            raise DomainError("expires_at must be in the future")
        return [
            ApiKeyIssued(
                aggregate_id=self.id,
                key_id=key_id,
                label=label.strip(),
                key_hash=key_hash,
                subject=subject,
                sealed=sealed,
                expires_at=expires_at,
            )
        ]

    def decide_reveal_key(self, *, key_id: str) -> list[Event]:
        """Append the fact that the plaintext was handed out.

        The wipe of the sealed copy is a separate event (`ApiKeySealedWiped` —
        not modelled: it is the projection of this one) because the log is
        append-only and the plaintext must stop being readable in the read
        model, not in the history.
        """
        self._ensure_live()
        if key_id not in self.api_key_ids:
            raise DomainError("key not found")
        return [ApiKeyRevealed(aggregate_id=self.id, key_id=key_id)]

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
