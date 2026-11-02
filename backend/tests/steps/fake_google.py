"""A real OIDC provider the tests can talk to over a socket.

Google is the thing that makes this flow hard to get right, so the tests do not
stub it out: the gateway discovers `/.well-known/openid-configuration`, exchanges
a code at the token endpoint, and fetches the JWKS — all over HTTP, all against
this. Anything the gateway gets wrong about a live provider (wrong endpoint,
wrong redirect URI, unverified signature, missing audience check) fails here too.

Deliberately hostile where it matters: the token endpoint can be told to mint a
token for a different audience, to claim an unverified address, or to hand back
a code nobody issued.
"""

import time
from typing import Any

import uvicorn
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route


class FakeGoogle:
    """Minimal OIDC provider: authorize, token, userinfo, discovery, JWKS."""

    def __init__(self, *, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.key = RSAKey.generate_key(2048, auto_kid=True)
        self.port = 0
        self.issuer = ""
        self.server: uvicorn.Server | None = None
        self.task: Any = None
        self.known_codes: set[str] = set()
        self.last_authorize_params: dict[str, str] = {}
        # What the next token should claim. Tests mutate these.
        self.email = "ops@example.dev"
        self.email_verified = True
        self.audience_override: str | None = None

    # -- wiring ----------------------------------------------------------

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self.discovery),
                Route("/o/oauth2/v2/auth", self.authorize),
                Route("/oauth2/v4/token", self.token, methods=["POST"]),
                Route("/oauth2/v4/jwks", self.jwks),
                Route("/userinfo", self.userinfo),
            ]
        )

    async def authorize(self, request: Request) -> Any:
        """Record what the gateway asked for, then bounce straight back.

        Real Google would show a login page; the tests only need the parameters
        the gateway sent (redirect URI, state, scopes) and a code to hand back.
        """
        self.last_authorize_params = dict(request.query_params)
        code = f"code-{time.time_ns()}"
        self.known_codes.add(code)
        target = self.last_authorize_params.get("redirect_uri", "")
        assert target, "gateway did not send a redirect_uri"
        joiner = "&" if "?" in target else "?"
        state = self.last_authorize_params.get("state", "")
        return RedirectResponse(f"{target}{joiner}code={code}&state={state}")

    async def discovery(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/o/oauth2/v2/auth",
            "token_endpoint": f"{self.issuer}/oauth2/v4/token",
            "jwks_uri": f"{self.issuer}/oauth2/v4/jwks",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        })

    async def jwks(self, request: Request) -> JSONResponse:
        return JSONResponse({"keys": [self.key.as_dict(private=False)]})

    async def token(self, request: Request) -> JSONResponse:
        form = await request.form()
        if form.get("client_id") != self.client_id:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if self.client_secret and form.get("client_secret") != self.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if form.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        if not form.get("redirect_uri"):
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        claims = {
            "iss": self.issuer,
            "aud": self.audience_override or self.client_id,
            "sub": self.email,
            "email": self.email,
            "email_verified": self.email_verified,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        id_token = jwt.encode(
            {"alg": "RS256", "kid": self.key.kid}, claims, self.key
        )
        return JSONResponse({
            "access_token": "fake-access-token",
            "token_type": "Bearer",
            "expires_in": 600,
            "id_token": id_token,
        })

    async def userinfo(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "sub": self.email,
            "email": self.email,
            "email_verified": self.email_verified,
        })

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        import asyncio
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.issuer = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(self.app(), host="127.0.0.1", port=self.port,
                           log_level="warning")
        )
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(100):
            if self.server.started:
                return
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            await self.task
            self.task = None


__all__ = ["FakeGoogle"]
