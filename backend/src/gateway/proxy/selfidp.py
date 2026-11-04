"""The deployment as its own identity provider.

Every other provider in `providers.py` is a *proxy*: the gateway forwards the
browser to an upstream IdP and the operator has to register a callback URI for
each gateway slug (`/g/rancher/auth/callback`, `/g/db/auth/callback`, …). Google
has no wildcard, so that list grows with every gateway and the gateway is not
portable.

`self` removes the upstream. The authorization server is this process, and the
human step is the operator session the panel already issues — no new account
system, no new redirect URIs to register anywhere. The client still speaks
standard OAuth 2.1 + PKCE + RFC 8707 (`resource`), so MCP clients need no
special case.

What is deliberately strict, because each is a real way this is broken:

* **audience** — a token is minted for one gateway's resource URL and verified
  against it, so a token earned on `rancher` is refused on `db` even though
  both are signed by the same deployment.
* **PKCE** — delegated to the SDK's token handler, not re-implemented here.
* **consent** — a client is never handed a code without the signed-in operator
  approving that specific transaction; the transaction id is a capability and
  the CSRF token is bound to it.
* **browser binding** — the transaction lives server-side and expires; the URL
  carries only its id, so a page reload cannot resurrect an old approval.

ponytail: approvals are per-transaction, not remembered per client. Add a
`remember` cookie (like `OAuthProxy`'s) if the one extra click becomes noise.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import quote

from fastmcp.server.auth.auth import ClientRegistrationOptions, OAuthProvider
from joserfc import jwt
from joserfc.jwk import OctKey
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl, BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from gateway.log import log
from gateway.proxy import branding

#: How long a client has to finish the browser leg before it starts again.
CONSENT_TTL_SECONDS = 5 * 60
CODE_TTL_SECONDS = 60
ACCESS_TTL_SECONDS = 60 * 60
REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60

#: The panel's session cookie; the only human credential this flow accepts.
_SESSION_COOKIE = "omg_session"


class _Consent(BaseModel):
    """One authorization request waiting for a human.

    Stored server-side and addressed by an unguessable id, because everything in
    it decides what a code may be exchanged for.
    """

    model_config = ConfigDict(frozen=True)

    txn: str
    csrf: str
    client_id: str
    client_name: str = ""
    redirect_uri: str
    state: str | None = None
    code_challenge: str
    scopes: list[str]
    resource: str
    expires_at: int


class DeploymentAuthServer(OAuthProvider):
    """OAuth authorization server whose upstream is the operator session.

    One instance per gateway: the issuer is `/g/<slug>`, which keeps discovery,
    metadata and (most importantly) the token audience per gateway.
    """

    def __init__(
        self,
        *,
        base_url: str,
        gateway_id: str,
        slug: str,
        gateway_name: str,
        signing_key: str,
        client_storage: Any,
        session_secret: str | None = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            issuer_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            required_scopes=required_scopes,
        )
        self._gateway_id = gateway_id
        self._slug = slug
        self._gateway_name = gateway_name
        self._session_secret = (
            session_secret
            if session_secret is not None
            else os.environ.get("OMG_SESSION_SECRET", "")
        )
        # HS256 with a key derived from the deployment secret: the verifier is
        # this same process, so there is no key to publish and no JWKS fetch on
        # the hot path. Per gateway, so two gateways cannot verify each other's
        # tokens even before the audience check.
        digest = hashlib.sha256(f"{signing_key}:{gateway_id}".encode()).digest()
        self._key = OctKey.import_key(base64.urlsafe_b64encode(digest).decode())

        from key_value.aio.adapters.pydantic import PydanticAdapter

        self._clients = PydanticAdapter(
            client_storage,
            OAuthClientInformationFull,
            default_collection="self-clients",
            raise_on_validation_error=True,
        )
        self._consents = PydanticAdapter(
            client_storage,
            _Consent,
            default_collection="self-consents",
            raise_on_validation_error=True,
        )
        self._codes = PydanticAdapter(
            client_storage,
            AuthorizationCode,
            default_collection="self-codes",
            raise_on_validation_error=True,
        )
        self._refresh = PydanticAdapter(
            client_storage,
            RefreshToken,
            default_collection="self-refresh",
            raise_on_validation_error=True,
        )

    # -- registration ------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("client_id is required")
        await self._clients.put(client_info.client_id, client_info)

    # -- browser leg -------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the consent screen.

        The redirect target is this gateway's own `/continue`; the human step
        happens there, where there is a `Request` to read the session cookie
        from. Nothing about the client's `redirect_uri` is trusted until then.
        """
        expected = self._audience()
        if params.resource and params.resource.rstrip("/") != expected.rstrip("/"):
            # RFC 8707: a token is for one resource. Refusing here keeps a
            # confused client from earning a token it will never be able to use.
            raise AuthorizeError(
                error="invalid_request",
                error_description="resource does not match this gateway",
            )
        if not client.client_id:
            raise AuthorizeError(error="invalid_client")

        txn = secrets.token_urlsafe(24)
        consent = _Consent(
            txn=txn,
            csrf=secrets.token_urlsafe(24),
            client_id=client.client_id,
            client_name=client.client_name or "",
            redirect_uri=str(params.redirect_uri),
            state=params.state,
            code_challenge=params.code_challenge,
            scopes=list(params.scopes or []),
            resource=expected,
            expires_at=int(time.time()) + CONSENT_TTL_SECONDS,
        )
        await self._consents.put(txn, consent, ttl=CONSENT_TTL_SECONDS)
        return f"{self.base_url}/continue?txn={quote(txn)}"

    def _audience(self) -> str:
        """The audience this gateway mints for: `<issuer>/mcp`."""
        base = str(self.base_url).rstrip("/")
        return f"{base}{self._mcp_path or '/mcp'}"

    async def _continue(self, request: Request) -> Response:
        """GET shows the approval page; POST approves or denies one transaction."""
        if request.method == "POST":
            return await self._decide(request)

        txn = request.query_params.get("txn", "")
        consent = await self._consents.get(txn) if txn else None
        if consent is None or consent.expires_at < int(time.time()):
            return self._error("This request expired", "Start again from your MCP client.")

        session = self._session(request)
        if session is None:
            # The panel owns signing in; send the browser there and come back.
            back = f"/g/{self._slug}/continue?txn={quote(txn)}"
            return RedirectResponse(f"/#/login?next={quote(back)}", status_code=303)

        return HTMLResponse(
            branding.consent_html(
                client_id=consent.client_id,
                client_name=consent.client_name or None,
                redirect_uri=consent.redirect_uri,
                scopes=consent.scopes,
                txn_id=consent.txn,
                csrf_token=consent.csrf,
                server_name=self._gateway_name,
                title="Allow this client?",
            )
        )

    async def _decide(self, request: Request) -> Response:
        form = await request.form()
        txn = str(form.get("txn_id", ""))
        consent = await self._consents.get(txn) if txn else None
        if consent is None or consent.expires_at < int(time.time()):
            return self._error("This request expired", "Start again from your MCP client.")
        if not secrets.compare_digest(str(form.get("csrf_token", "")), consent.csrf):
            return self._error("This page is stale", "Start again from your MCP client.")
        if self._session(request) is None:
            return self._error("Sign in first", "Your panel session ended mid-flow.")

        await self._consents.delete(txn)
        if str(form.get("action", "")) != "approve":
            return RedirectResponse(
                construct_redirect_uri(
                    consent.redirect_uri,
                    error="access_denied",
                    error_description="the operator denied the request",
                    state=consent.state,
                ),
                status_code=302,
            )

        code = secrets.token_urlsafe(32)
        subject = self._session_subject(request)
        await self._codes.put(
            code,
            AuthorizationCode(
                code=code,
                scopes=consent.scopes,
                expires_at=time.time() + CODE_TTL_SECONDS,
                client_id=consent.client_id,
                code_challenge=consent.code_challenge,
                redirect_uri=AnyUrl(consent.redirect_uri),
                redirect_uri_provided_explicitly=True,
                resource=consent.resource,
                subject=subject,
            ),
            ttl=CODE_TTL_SECONDS,
        )
        log.info(
            "selfidp.approved",
            gateway=self._gateway_id,
            client_id=consent.client_id,
            subject=subject,
        )
        return RedirectResponse(
            construct_redirect_uri(
                consent.redirect_uri, code=code, state=consent.state
            ),
            status_code=302,
        )

    def _session_subject(self, request: Request) -> str:
        session = self._session(request)
        return session.email if session else ""

    def _session(self, request: Request) -> Any:
        from gateway.interface.sessions import SessionError, read_session

        token = request.cookies.get(_SESSION_COOKIE) or ""
        if not token or not self._session_secret:
            return None
        try:
            return read_session(token, secret=self._session_secret)
        except SessionError:
            return None

    def _error(self, title: str, message: str) -> HTMLResponse:
        return HTMLResponse(
            branding.error_html(title, message, server_name=self._gateway_name),
            status_code=400,
        )

    # -- token leg ---------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        stored = await self._codes.get(authorization_code)
        if stored is None or stored.client_id != client.client_id:
            return None
        if stored.expires_at < time.time():
            return None
        return stored

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single use: deleting before minting means a replayed code cannot be
        # exchanged twice even if the two requests race.
        await self._codes.delete(authorization_code.code)
        return await self._issue(
            client_id=str(client.client_id),
            subject=authorization_code.subject or "",
            scopes=list(authorization_code.scopes),
            resource=str(authorization_code.resource or self._audience()),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        stored = await self._refresh.get(refresh_token)
        if stored is None or stored.client_id != client.client_id:
            return None
        if stored.expires_at is not None and stored.expires_at < int(time.time()):
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        return await self._issue(
            client_id=str(client.client_id),
            subject=refresh_token.subject or "",
            scopes=scopes or list(refresh_token.scopes),
            resource=str(refresh_token.resource or self._audience()),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            decoded = jwt.decode(token, self._key, algorithms=["HS256"])
        except Exception:
            return None
        claims = dict(decoded.claims)
        if claims.get("iss") != str(self.issuer_url):
            return None
        audience = claims.get("aud")
        if isinstance(audience, list):
            audience = audience[0] if audience else None
        if audience != self._audience():
            return None
        expires_at = int(claims.get("exp", 0))
        if expires_at < int(time.time()):
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or ""),
            scopes=str(claims.get("scope") or "").split(),
            expires_at=expires_at,
            subject=str(claims.get("sub") or ""),
            claims=claims,
        )

    async def revoke_token(self, token: Any) -> None:
        if isinstance(token, RefreshToken):
            await self._refresh.delete(token.token)
        else:
            # Access tokens are self-contained and short-lived; there is nothing
            # server-side to delete, and pretending otherwise would be a lie.
            log.info("selfidp.revoke_stateless", gateway=self._gateway_id)

    async def _issue(
        self, *, client_id: str, subject: str, scopes: list[str], resource: str
    ) -> OAuthToken:
        now = int(time.time())
        refresh_token = secrets.token_urlsafe(32)
        await self._refresh.put(
            refresh_token,
            RefreshToken(
                token=refresh_token,
                client_id=client_id,
                scopes=scopes,
                expires_at=now + REFRESH_TTL_SECONDS,
                resource=resource,
                subject=subject,
            ),
            ttl=REFRESH_TTL_SECONDS,
        )
        access = jwt.encode(
            {"alg": "HS256"},
            {
                "iss": str(self.issuer_url),
                "aud": resource,
                "sub": subject,
                "email": subject,
                "client_id": client_id,
                "scope": " ".join(scopes),
                "iat": now,
                "exp": now + ACCESS_TTL_SECONDS,
                "jti": secrets.token_hex(8),
            },
            self._key,
            algorithms=["HS256"],
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_SECONDS,
            scope=" ".join(scopes) or None,
            refresh_token=refresh_token,
        )

    # -- routes ------------------------------------------------------------

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """The standard OAuth routes, plus the consent screen they point at."""
        routes = super().get_routes(mcp_path)
        routes.append(
            Route("/continue", endpoint=self._continue, methods=["GET", "POST"])
        )
        return routes


__all__ = ["ACCESS_TTL_SECONDS", "DeploymentAuthServer"]
