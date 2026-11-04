"""Who is calling a gateway, and whether that is good enough.

`Identity` is the result of a check, not state: it is frozen because a request
must not be able to talk itself into a different subject halfway through.
"""

from typing import Any

from fastmcp.server.auth import AuthProvider
from pydantic import BaseModel, ConfigDict

from gateway.domain.events import AuthMode
from gateway.infrastructure.eventstore.projections import ReadModels


class Identity(BaseModel):
    """Who is calling. Immutable: it is the result of a check, not state."""

    model_config = ConfigDict(frozen=True)

    subject: str
    display: str = ""
    #: Which credential source accepted this token: an identity provider
    #: (`google`, `github`, `oidc`), a verifier label, `api_key`, or `none`.
    #: It is what per-source tool scopes are keyed by, and what traffic is
    #: attributed to — "which provider let this in" is the first question after
    #: a refusal.
    source: str = "anonymous"


class IdentityDenied(LookupError):
    """Credential was present but rejected."""


class GatewayNotReady(RuntimeError):
    """The gateway is configured but cannot serve yet (missing credentials).

    A draft in this state is kept out of the route table entirely, so the route
    answers 404 instead of becoming an accidentally public proxy.
    """


class GatewayAuth:
    """Resolves a bearer token into an Identity for one gateway.

    More than one credential path can be open at once, so `resolve` is a small
    chain rather than a `match` on the mode:

    * an API key, when the gateway issues keys at all;
    * the OAuth provider's own `verify_token` — which, when the gateway was
      built with extra issuers, is `MultiAuth` and tries each of them in turn.

    The order matters only for speed (a hash lookup before a signature check),
    never for correctness: a token that is not a key falls through. OAuth
    verification is delegated so there is exactly one implementation of "is
    this token good", and the proxy layer cannot drift from the transport layer.
    """

    def __init__(self, config: dict[str, Any], read_models: ReadModels,
                 provider: AuthProvider | None) -> None:
        self._config = config
        self._read_models = read_models
        self._provider = provider
        self.mode = AuthMode(config["auth_mode"])
        #: True when this gateway hosts a login flow, which is what decides the
        #: `WWW-Authenticate` challenge the guard sends.
        self.hosts_login = self.mode is AuthMode.OAUTH

    async def resolve(self, token: str | None) -> Identity | None:
        """Return the caller's identity, or None when no credential is valid.

        Two paths, never both: a pure key gateway verifies the hash itself, and
        every other gateway delegates to its provider — which, when the operator
        allowed keys or extra issuers, is a `MultiAuth` already trying each of
        them. Delegating keeps one implementation of "is this credential good"
        and, more importantly, one that the transport agrees with.
        """
        if self.mode is AuthMode.NONE:
            return Identity(subject="anonymous", source="none")
        if not token:
            return None
        if self.mode is AuthMode.API_KEY:
            from gateway.infrastructure.security import hash_api_key

            subject = await self._read_models.subject_for_key_hash(
                self._config["id"], hash_api_key(token)
            )
            return Identity(subject=subject, source="api_key") if subject else None
        if self._provider is None:
            return None
        access = await self._provider.verify_token(token)
        if access is None:
            return None
        claims = getattr(access, "claims", {}) or {}
        # Prefer a human-stable identifier so allowlists stay readable.
        subject = (
            claims.get("login")
            or claims.get("email")
            or claims.get("preferred_username")
            or access.subject
            or access.client_id
        )
        return Identity(
            subject=str(subject),
            display=str(claims.get("name") or claims.get("email") or ""),
            source=_source_of(
                access,
                key_subject=access.subject,
                hosted=self._config.get("idp") or self.mode.value,
            ),
        )

def _source_of(access: Any, *, key_subject: str | None, hosted: str) -> str:
    """The source that accepted this token, for the traffic log and tool scopes.

    `MultiAuth` prefixes the client id with the label it was configured with
    (`["corp", ...]`), so a call can be attributed to *which* provider let it in
    instead of a flat "oauth". A key is not labelled by `MultiAuth`; its subject
    is already `api-key:<id>`, which is the more useful thing to log.

    `hosted` names the gateway's own login provider (`google`, `github`, …) and
    is what an unlabelled token — one the hosted server minted — resolves to.
    Without it, every login would report the same flat "oauth" and a per-source
    tool scope could not tell Google from the GitHub verifier.
    """
    if key_subject and key_subject.startswith("api-key:"):
        return "api_key"
    client_id = getattr(access, "original_client_id", None) or getattr(
        access, "client_id", None
    )
    if not isinstance(client_id, str) or not client_id.startswith("["):
        return hosted
    try:
        import json

        parts = json.loads(client_id)
    except ValueError:
        return hosted
    return str(parts[0]) if isinstance(parts, list) and parts else hosted

def is_key_subject(subject: str) -> bool:
    """True for the subject a gateway-minted API key resolves to.

    One predicate for both places that decide who gets in, so the guard and the
    domain cannot drift on what "this is a key, not a person" means.
    """
    return subject.startswith("api-key:")
