"""Steps for gateways whose identity provider is the deployment itself.

Every scenario drives the real ASGI app the router mounts — discovery,
registration, consent, the token exchange — because the whole point of `self` is
that there is no upstream to be lenient for us.

The negatives carry the weight here: this provider mints tokens, so "a token from
another gateway is refused" and "a code is spent once" are exactly the claims a
hand-written authorization server gets wrong.
"""

import asyncio
import hashlib
import re
from base64 import urlsafe_b64encode

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.interface.sessions import issue_session
from gateway.proxy.providers import build_auth_provider
from tests.steps.support import Context

scenarios("../features/self_idp.feature")

SECRET = "test-secret-key-at-least-32-bytes-long!!"
REDIRECT_URI = "http://127.0.0.1:9999/callback"


@pytest.fixture(autouse=True)
def deployment_secret(monkeypatch) -> None:
    """`self` authorises with the operator session, whose key is the deployment
    secret. In production that is an env var; here it is set once."""
    monkeypatch.setenv("OMG_SESSION_SECRET", SECRET)


@pytest.fixture()
def ctx() -> Context:
    return Context()


def _config(slug: str) -> dict:
    return {
        "id": f"gw_{slug}",
        "slug": slug,
        "name": slug.title(),
        "auth_mode": "oauth",
        "idp": "self",
    }


def _provider(slug: str):
    provider = build_auth_provider(
        _config(slug),
        base_url="https://gw.example.dev",
        credentials={"jwt_signing_key": "k" * 64},
    )
    assert provider is not None
    provider.set_mcp_path("/mcp")
    return provider


def _app(provider, slug: str):
    from fastmcp import FastMCP
    from starlette.applications import Starlette
    from starlette.routing import Mount

    inner = FastMCP(name=f"gateway:{slug}", auth=provider).http_app(path="/mcp")
    return Starlette(routes=[Mount(f"/g/{slug}", app=inner)])


def _client(ctx: Context, slug: str = "demo") -> httpx.AsyncClient:
    provider = ctx.extra.setdefault("providers", {}).get(slug) or _provider(slug)
    ctx.extra["providers"][slug] = provider
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(provider, slug)),
        base_url=f"https://gw.example.dev/g/{slug}",
    )


def _pkce() -> str:
    return urlsafe_b64encode(hashlib.sha256(b"v" * 64).digest()).decode().rstrip("=")


def _session(email: str) -> str:
    return issue_session(
        secret=SECRET, operator_id="op_1", email=email, is_admin=True
    )


async def _register(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/register",
        json={
            "client_name": "probe",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201, response.text[:300]
    return response.json()["client_id"]


async def _start(client: httpx.AsyncClient, client_id: str, **extra: str) -> str:
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": _pkce(),
            "code_challenge_method": "S256",
            "state": "s1",
            **extra,
        },
        follow_redirects=False,
    )
    if response.status_code == 302:
        location = response.headers["location"]
        # A refusal also answers 302, but it goes back to the *client's*
        # callback with an `error`; only our own consent URL means "go on".
        if "/continue?txn=" in location:
            return location
    return ""


async def _fields(client: httpx.AsyncClient, location: str) -> dict[str, str]:
    response = await client.get(location, follow_redirects=False)
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
    return dict(re.findall(r'name="(\w+)" value="([^"]*)"', response.text))


async def _exchange(client: httpx.AsyncClient, client_id: str, code: str):
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": "v" * 64,
        },
    )


# -- given -----------------------------------------------------------------


@given(
    parsers.parse('a gateway "{slug}" whose identity provider is this deployment'),
    target_fixture="gateway",
)
def gateway_with_self_idp(ctx: Context, slug: str) -> dict:
    ctx.extra.setdefault("providers", {})[slug] = _provider(slug)
    ctx.slug = slug
    return _config(slug)


@given(parsers.parse('a second gateway "{slug}" whose identity provider is this deployment'))
def second_gateway(ctx: Context, slug: str) -> None:
    ctx.extra.setdefault("providers", {})[slug] = _provider(slug)


@given(parsers.parse('an operator signed in to the panel as "{email}"'))
def signed_in(ctx: Context, email: str) -> None:
    ctx.extra["email"] = email
    ctx.extra["session"] = _session(email)


@given("the operator is not signed in")
def not_signed_in(ctx: Context) -> None:
    ctx.extra["session"] = ""


# -- when ------------------------------------------------------------------


@when("the client registers, is sent to consent, and approves")
def register_and_approve(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            if ctx.extra.get("session"):
                client.cookies.set("omg_session", ctx.extra["session"])
            client_id = await _register(client)
            location = await _start(client, client_id)
            page = await _fields(client, location)
            approved = await client.post(
                location.split("?")[0],
                data={**page, "action": "approve"},
                follow_redirects=False,
            )
            assert approved.status_code == 302, approved.text[:300]
            ctx.extra["redirect"] = approved.headers["location"]
            ctx.extra["client_id"] = client_id

    asyncio.run(scenario())


@when("the client registers, is sent to consent, and denies")
def register_and_deny(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            client.cookies.set("omg_session", ctx.extra["session"])
            client_id = await _register(client)
            location = await _start(client, client_id)
            page = await _fields(client, location)
            denied = await client.post(
                location.split("?")[0],
                data={**page, "action": "deny"},
                follow_redirects=False,
            )
            assert denied.status_code == 302, denied.text[:300]
            ctx.extra["redirect"] = denied.headers["location"]

    asyncio.run(scenario())


@when("the client registers and reaches the consent screen")
def register_without_session(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            client_id = await _register(client)
            response = await client.get(
                await _start(client, client_id), follow_redirects=False
            )
            ctx.response = response

    asyncio.run(scenario())


@when("the client registers, is sent to consent, and posts a forged CSRF token")
def forged_csrf(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            client.cookies.set("omg_session", ctx.extra["session"])
            client_id = await _register(client)
            location = await _start(client, client_id)
            page = await _fields(client, location)
            ctx.response = await client.post(
                location.split("?")[0],
                data={**page, "csrf_token": "forged", "action": "approve"},
                follow_redirects=False,
            )

    asyncio.run(scenario())


@when("the browser opens a consent screen for a transaction that does not exist")
def unknown_txn(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            client.cookies.set("omg_session", ctx.extra["session"])
            ctx.response = await client.get(
                "/continue?txn=made-up", follow_redirects=False
            )

    asyncio.run(scenario())


@when("the client completes the flow and exchanges the code twice")
def double_exchange(ctx: Context) -> None:
    register_and_approve(ctx)
    code = httpx.URL(ctx.extra["redirect"]).params["code"]

    async def scenario() -> None:
        async with _client(ctx) as client:
            first = await _exchange(client, ctx.extra["client_id"], code)
            ctx.extra["access"] = first.json().get("access_token")
            ctx.response = await _exchange(client, ctx.extra["client_id"], code)

    asyncio.run(scenario())


@when(parsers.parse('the client earns a token on "{slug}"'))
def earns_token(ctx: Context, slug: str) -> None:
    register_and_approve(ctx)

    async def scenario() -> None:
        code = httpx.URL(ctx.extra["redirect"]).params["code"]
        async with _client(ctx) as client:
            response = await _exchange(client, ctx.extra["client_id"], code)
            assert response.status_code == 200, response.text[:300]
            ctx.extra["access"] = response.json()["access_token"]

    asyncio.run(scenario())


@when("the client asks to authorize for another gateway's resource")
def wrong_resource(ctx: Context) -> None:
    async def scenario() -> None:
        async with _client(ctx) as client:
            client.cookies.set("omg_session", ctx.extra["session"])
            client_id = await _register(client)
            ctx.extra["authorize"] = await _start(
                client, client_id, resource="https://gw.example.dev/g/other/mcp"
            )

    asyncio.run(scenario())


@when("the gateway is shown a token that expired")
def expired_token(ctx: Context) -> None:
    from joserfc import jwt

    provider = ctx.extra["providers"]["demo"]
    ctx.extra["expired"] = jwt.encode(
        {"alg": "HS256"},
        {
            "iss": str(provider.issuer_url),
            "aud": "https://gw.example.dev/g/demo/mcp",
            "exp": 1,
            "sub": "ops@example.dev",
        },
        provider._key,
        algorithms=["HS256"],
    )


@when("the client completes the flow and refreshes")
def refresh(ctx: Context) -> None:
    register_and_approve(ctx)

    async def scenario() -> None:
        code = httpx.URL(ctx.extra["redirect"]).params["code"]
        async with _client(ctx) as client:
            first = await _exchange(client, ctx.extra["client_id"], code)
            renewed = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": first.json()["refresh_token"],
                    "client_id": ctx.extra["client_id"],
                },
            )
            assert renewed.status_code == 200, renewed.text[:300]
            ctx.extra["access"] = renewed.json()["access_token"]

    asyncio.run(scenario())


# -- then ------------------------------------------------------------------


@then("the token endpoint issues an access token")
def issued(ctx: Context) -> None:
    async def scenario() -> None:
        code = httpx.URL(ctx.extra["redirect"]).params["code"]
        async with _client(ctx) as client:
            response = await _exchange(client, ctx.extra["client_id"], code)
            assert response.status_code == 200, response.text[:300]
            body = response.json()
            assert body["token_type"] == "Bearer"
            ctx.extra["access"] = body["access_token"]

    asyncio.run(scenario())


@then(parsers.parse('the gateway accepts that token as "{subject}"'))
def accepted(ctx: Context, subject: str) -> None:
    async def scenario() -> None:
        token = await ctx.extra["providers"]["demo"].verify_token(ctx.extra["access"])
        assert token is not None, "the issuing gateway rejected its own token"
        assert token.subject == subject

    asyncio.run(scenario())


@then("the browser is redirected to the panel's login")
def redirected_to_login(ctx: Context) -> None:
    assert ctx.response.status_code == 303, ctx.response.text[:300]
    assert ctx.response.headers["location"].startswith("/#/login?next=")


@then(parsers.parse('the client is redirected with error "{error}"'))
def redirect_error(ctx: Context, error: str) -> None:
    location = ctx.extra["redirect"]
    assert location.startswith(REDIRECT_URI), location
    assert httpx.URL(location).params["error"] == error


@then("no code is issued")
def no_code(ctx: Context) -> None:
    location = ctx.extra.get("redirect", "")
    if location:
        assert "code" not in httpx.URL(location).params
    if ctx.response is not None:
        assert "code=" not in ctx.response.text


@then("the consent screen refuses the submission")
def consent_refused(ctx: Context) -> None:
    assert ctx.response.status_code == 400, ctx.response.text[:300]


@then("the second exchange is refused")
def second_refused(ctx: Context) -> None:
    assert ctx.response.status_code >= 400, ctx.response.text[:300]


@then(parsers.parse('"{slug}" accepts the token'))
def gateway_accepts(ctx: Context, slug: str) -> None:
    async def scenario() -> None:
        token = await ctx.extra["providers"][slug].verify_token(ctx.extra["access"])
        assert token is not None

    asyncio.run(scenario())


@then(parsers.parse('"{slug}" refuses it'))
def gateway_refuses(ctx: Context, slug: str) -> None:
    async def scenario() -> None:
        token = await ctx.extra["providers"][slug].verify_token(ctx.extra["access"])
        assert token is None, "a token minted for one gateway was accepted by another"

    asyncio.run(scenario())


@then("the authorization request is refused")
def authorize_refused(ctx: Context) -> None:
    assert not ctx.extra.get("authorize"), (
        f"authorize for a foreign resource produced {ctx.extra.get('authorize')}"
    )


@then("the gateway refuses it")
def refuses_expired(ctx: Context) -> None:
    async def scenario() -> None:
        token = await ctx.extra["providers"]["demo"].verify_token(
            ctx.extra["expired"]
        )
        assert token is None

    asyncio.run(scenario())


@then(parsers.parse('the refreshed token is accepted as "{subject}"'))
def refreshed_accepted(ctx: Context, subject: str) -> None:
    accepted(ctx, subject)
