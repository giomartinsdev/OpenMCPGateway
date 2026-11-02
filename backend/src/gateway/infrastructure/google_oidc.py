"""Google OIDC sign-in for the admin UI — the authorization-code flow.

Why hand-rolled instead of FastMCP's `GoogleProvider`: that one guards *MCP
routes* and mints its own access tokens for them. The admin UI needs a browser
cookie, not an MCP token, and reusing the proxy would mean two unrelated
credential systems answering to the same session.

What is deliberately strict here, because each is a real way this flow is broken:

* **state** — bound to the browser by a short-lived signed cookie, so a callback
  cannot be replayed into someone else's session.
* **audience** — a Google project signs tokens for every client in it; skipping
  `aud` lets a token minted for an unrelated app sign in here.
* **email_verified** — an unverified address is a claim, not an identity.
* **allowlist** — sign-in creates an operator, so an open flow hands out admin.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict

STATE_COOKIE = "omg_login_state"
STATE_TTL_SECONDS = 10 * 60
CALLBACK_PATH = "/api/auth/google/callback"


class LoginError(RuntimeError):
    """The sign-in attempt is not trustworthy; say why, do not continue."""


class Identity(BaseModel):
    """What the provider vouched for, once verified."""

    model_config = ConfigDict(frozen=True)

    email: str
    subject: str


# -- state binding ---------------------------------------------------------

def issue_state(*, secret: str, redirect_uri: str) -> str:
    """A signed triple: nonce, expiry, and the redirect this login started with."""
    nonce = secrets.token_urlsafe(24)
    body = {"nonce": nonce, "exp": int(time.time()) + STATE_TTL_SECONDS,
            "redirect_uri": redirect_uri}
    encoded = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":")).encode()
    ).decode()
    return f"{encoded}.{_sign(encoded, secret)}"


def read_state(token: str, *, secret: str) -> dict[str, Any]:
    encoded, _, signature = token.partition(".")
    if not encoded or not signature:
        raise LoginError("malformed login state")
    if not hmac.compare_digest(signature, _sign(encoded, secret)):
        raise LoginError("login state signature does not match")
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise LoginError("undecodable login state") from exc
    if int(body.get("exp", 0)) < int(time.time()):
        raise LoginError("login state expired — start again")
    return body


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


# -- provider --------------------------------------------------------------

class GoogleOIDC:
    """Discovery, code exchange and ID-token verification against one issuer."""

    def __init__(self, *, client_id: str, client_secret: str,
                 discovery_url: str, redirect_uri: str | None = None,
                 base_url: str = "",
                 http: httpx.AsyncClient | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.discovery_url = discovery_url
        # The operator types this in the panel because it must match the provider
        # byte for byte. `base_url` remains for callers that want the default.
        self.redirect_uri = redirect_uri or f"{base_url.rstrip('/')}{CALLBACK_PATH}"
        self._http = http
        self._metadata: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            async with self._client() as client:
                response = await client.get(self.discovery_url)
                response.raise_for_status()
                self._metadata = response.json()
        return self._metadata

    def _client(self) -> httpx.AsyncClient:
        # An injected client is for tests; otherwise one per call is fine, these
        # are two requests per sign-in, not a hot path.
        return self._http or httpx.AsyncClient(timeout=10.0)

    async def authorization_url(self, *, state: str) -> str:
        meta = await self.metadata()
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # Without this Google may answer with a code for a session the user
            # never chose.
            "prompt": "select_account",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def exchange(self, *, code: str) -> Identity:
        meta = await self.metadata()
        async with self._client() as client:
            response = await client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            if response.status_code != 200:
                raise LoginError(
                    f"token exchange refused: {response.status_code} "
                    f"{response.text[:200]}"
                )
            id_token = response.json().get("id_token")
            if not id_token:
                raise LoginError("provider returned no id_token")

            key_set = await self._keys()
            claims = self._verify(id_token, key_set, issuer=meta["issuer"])

            if self._audience_of(claims) != self.client_id:
                raise LoginError("id_token was minted for another application")
            email = str(claims.get("email") or "")
            if not email:
                raise LoginError("id_token carries no email")
            if claims.get("email_verified") is not True:
                raise LoginError(f"'{email}' is not verified by the provider")
            return Identity(email=email.lower(),
                            subject=str(claims.get("sub") or email))

    async def _keys(self) -> dict[str, Any]:
        if self._jwks is None:
            meta = await self.metadata()
            async with self._client() as client:
                response = await client.get(meta["jwks_uri"])
                response.raise_for_status()
                self._jwks = response.json()
        return self._jwks

    def _verify(self, id_token: str, key_set: dict[str, Any],
                *, issuer: str) -> dict[str, Any]:
        from joserfc import jwt
        from joserfc.jwk import KeySet

        try:
            token = jwt.decode(id_token, KeySet.import_key_set(key_set),
                               algorithms=["RS256"])
        except Exception as exc:
            raise LoginError(f"id_token is not verifiable: {exc}") from exc

        claims = dict(token.claims)
        if claims.get("iss") != issuer:
            raise LoginError(f"id_token issuer is not '{issuer}'")
        if int(claims.get("exp", 0)) < int(time.time()):
            raise LoginError("id_token expired")
        return claims

    def _audience_of(self, claims: dict[str, Any]) -> str | None:
        audience = claims.get("aud")
        if isinstance(audience, list):
            # A multi-audience token is only acceptable if we are one of them,
            # but for a sign-in we require it to be *about* us, so take the only
            # value we would accept.
            return self.client_id if self.client_id in audience else None
        return audience


__all__ = [
    "CALLBACK_PATH",
    "STATE_COOKIE",
    "STATE_TTL_SECONDS",
    "GoogleOIDC",
    "Identity",
    "LoginError",
    "issue_state",
    "read_state",
]
