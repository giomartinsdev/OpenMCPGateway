"""End to end: a token earned through the OAuth proxy survives a router refresh.

Everything above this file tests pieces. This walks the real chain the way a
client does — discovery, registration, consent, the upstream exchange, the
token endpoint — and then repeats the request that the router makes on every
path it does not recognise, because that is what production actually does
between "login succeeded" and "the very next call got a 401".

The identity provider runs in its own thread with its own event loop:
`OIDCProxy` resolves the discovery document synchronously in `__init__`, so a
provider served by the same loop would deadlock waiting on itself.
"""

import hashlib
import socket
import threading
import time
from base64 import urlsafe_b64encode

import httpx
import pytest
import uvicorn

from gateway.proxy.providers import build_auth_provider
from tests.steps.fake_google import FakeGoogle

CLIENT_ID = "omg-oauth-client.apps.googleusercontent.com"
CLIENT_SECRET = "omg-oauth-client-secret"


class ThreadedGoogle(FakeGoogle):
    """`FakeGoogle` served from a plain thread, so sync discovery can reach it."""

    def start_blocking(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.issuer = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            self.app(), host="127.0.0.1", port=self.port, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        assert self.server.started, "identity provider did not start"

    def stop_blocking(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture()
def idp() -> ThreadedGoogle:
    provider = ThreadedGoogle(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    provider.start_blocking()
    try:
        yield provider
    finally:
        provider.stop_blocking()


CONFIG = {
    "id": "gw_oauth_e2e",
    "slug": "demo",
    "auth_mode": "oauth",
    "idp": "oidc",
    "required_scopes": ["openid", "email"],
}


def _provider(idp: ThreadedGoogle):
    provider = build_auth_provider(
        CONFIG,
        base_url="https://gw.example.dev",
        credentials={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "config_url": f"{idp.issuer}/.well-known/openid-configuration",
            "jwt_signing_key": "k" * 64,
        },
    )
    assert provider is not None
    provider.set_mcp_path("/mcp")
    return provider


def _pkce() -> str:
    verifier = "v" * 64
    return (
        urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


async def _consent(client, url: str):
    import re

    page = await client.get(url, follow_redirects=False)
    assert page.status_code == 200, f"{page.status_code}: {page.text[-300:]}"
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', page.text))
    assert fields, "consent page has no form fields"
    return await client.post(
        url.split("?")[0], data={**fields, "action": "approve"},
        follow_redirects=False,
    )


async def _login(provider, idp: ThreadedGoogle) -> str:
    """Drive the provider's own endpoints; return the access token it issued."""
    app = _asgi(provider)
    base = "https://gw.example.dev/g/demo"
    redirect_uri = "http://127.0.0.1:9999/callback"
    challenge = _pkce()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base
    ) as client:
        client.headers["x-forwarded-proto"] = "https"
        registered = await client.post(
            "/register",
            json={
                "client_name": "probe",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert registered.status_code in (200, 201), registered.text[:300]
        client_id = registered.json()["client_id"]

        started = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "s1",
            },
            follow_redirects=False,
        )
        assert started.status_code == 302, started.text[:300]

        approved = await _consent(client, started.headers["location"])
        assert approved.status_code == 302, approved.text[:400]
        upstream = approved.headers["location"]

        # The IdP authorises immediately and redirects to our callback; that
        # request belongs to the IdP's own server, not to the gateway's ASGI app.
        async with httpx.AsyncClient() as external:
            idp_reply = await external.get(upstream, follow_redirects=False)
        assert idp_reply.is_redirect, idp_reply.text[:400]

        # Follow it back into the gateway, which is what the browser does.
        back = httpx.URL(idp_reply.headers["location"])
        callback = await client.get(str(back), follow_redirects=False)
        assert callback.status_code == 302, callback.text[:400]
        code = httpx.URL(callback.headers["location"]).params["code"]

        token = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": "v" * 64,
            },
        )
        assert token.status_code == 200, token.text[:400]
        return token.json()["access_token"]


def _asgi(provider):
    """The provider's app, mounted at `/g/demo` exactly as the router mounts it."""
    from fastmcp import FastMCP
    from starlette.applications import Starlette
    from starlette.routing import Mount

    inner = FastMCP(name="gateway:demo", auth=provider).http_app(path="/mcp")
    return Starlette(routes=[Mount("/g/demo", app=inner)])


def test_a_token_survives_the_reinstall_a_refresh_causes(idp: ThreadedGoogle) -> None:
    """Issue a token, rebuild the provider the way `Router.refresh()` does, use it.

    Without a shared store the rebuild is invisible to the signature check and
    fatal to the lookup, which is the exact production symptom: `verify_token`
    answers None for a token it minted itself.
    """
    import asyncio

    async def scenario() -> None:
        first = _provider(idp)
        access = await _login(first, idp)

        # What `Router.refresh()` does on every config reload and unknown path.
        second = _provider(idp)

        assert await first.verify_token(access) is not None, (
            "the issuing provider rejected its own token"
        )
        assert await second.verify_token(access) is not None, (
            "a rebuild orphaned a token that was already issued"
        )

    asyncio.run(scenario())
