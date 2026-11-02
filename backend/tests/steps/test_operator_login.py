"""Steps for operator sign-in through Google.

The provider is a real HTTP server (`FakeGoogle`), so these scenarios exercise
the same discovery, code exchange and token verification a live Google would.
Nothing about the flow is stubbed at the boundary being tested.
"""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gateway.application.container import Container
from gateway.domain.events import TENANT_OF_DEPLOYMENT
from tests.steps.fake_google import FakeGoogle

scenarios("../features/operator_login.feature")

CLIENT_ID = "omg-test-client.apps.googleusercontent.com"
CLIENT_SECRET = "omg-test-client-secret"
ALLOWED = ("ops@example.dev",)


@pytest.fixture()
def google(runner) -> FakeGoogle:
    """Start the provider on the same loop the ASGI app runs on.

    Two event loops cannot share a socket: uvicorn's server and the gateway's
    httpx client both live on `runner`'s loop, so the provider must too.
    """
    provider = FakeGoogle(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    runner.run(provider.start())
    try:
        yield provider
    finally:
        runner.run(provider.stop())


@pytest.fixture()
def container(runner, google, postgres) -> Container:
    """A container whose operator login trusts the fake provider.

    Overrides the suite's container because the provider's address is only known
    once it is listening.
    """
    from gateway.config import Settings

    settings = Settings(
        database_url=postgres,
        session_secret="test-secret-key-at-least-32-bytes-long!!",
        base_url="https://gateway.test",
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        google_discovery_url=f"{google.issuer}/.well-known/openid-configuration",
        operator_allowlist=ALLOWED,
    )
    instance = runner.run(Container.create(settings))
    try:
        yield instance
    finally:
        runner.run(instance.close())


@pytest.fixture()
def browser(live_server: str) -> httpx.Client:
    client = httpx.Client(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


@given("Google sign-in is configured for the gateway")
def google_configured(google: FakeGoogle) -> None:
    assert google.issuer.startswith("http://127.0.0.1:"), google.issuer


@given(parsers.parse('the identity provider will vouch for "{email}"'))
def vouches_for(google: FakeGoogle, email: str) -> None:
    google.email = email


@given("the identity provider mints tokens for another application")
def other_audience(google: FakeGoogle) -> None:
    google.audience_override = "someone-elses-client-id"


@given("the identity provider reports the address as unverified")
def unverified(google: FakeGoogle) -> None:
    google.email_verified = False


PUBLIC_BASE = "https://gateway.test"


def _to_live(client: httpx.Client, url: str) -> str:
    """Rewrite the gateway's *public* origin to the test server's.

    The gateway publishes `settings.base_url` (as it must, or a browser would be
    sent to a host that does not exist) and puts that same base inside the
    `redirect_uri` it hands the provider, so the provider sends the browser back
    there. Both spellings — plain and URL-encoded — must point at the local
    server during a test. The provider's own URLs already work and are untouched.
    """
    base = str(client.base_url).rstrip("/")
    if PUBLIC_BASE not in url:
        return url
    return (
        url.replace(PUBLIC_BASE, base)
        .replace(PUBLIC_BASE.replace("://", "%3A%2F%2F"), base.replace("://", "%3A%2F%2F"))
    )


@pytest.fixture()
def login_start(browser: httpx.Client) -> httpx.Response:
    """The first hop, requested on demand so steps can share it."""
    return browser.get("/api/auth/google/start", follow_redirects=False)


@when("an operator starts a Google login")
def start_login(login_start: httpx.Response) -> httpx.Response:
    return login_start


@then("the browser is redirected to the identity provider")
def redirected_to_idp(login_start: httpx.Response, google: FakeGoogle) -> None:
    start = login_start
    assert start.status_code in (302, 307), f"{start.status_code}: {start.text[:200]}"
    location = start.headers.get("location", "")
    assert location.startswith(google.issuer), location


@then("the redirect carries the gateway's callback URL")
def redirect_has_callback(login_start: httpx.Response) -> None:
    location = login_start.headers.get("location", "")
    assert "redirect_uri=" in location, location
    assert "%2Fapi%2Fauth%2Fgoogle%2Fcallback" in location, location


@then("the redirect carries a state value")
def redirect_has_state(login_start: httpx.Response) -> None:
    location = login_start.headers.get("location", "")
    assert "state=" in location, location


@when("the operator completes a Google login", target_fixture="completed")
def complete_login(browser: httpx.Client, login_start: httpx.Response,
                   google: FakeGoogle) -> list[httpx.Response]:
    return _walk(browser, login_start)


@when("the operator completes another Google login", target_fixture="completed")
def complete_login_again(browser: httpx.Client, google: FakeGoogle
                         ) -> list[httpx.Response]:
    started = browser.get("/api/auth/google/start", follow_redirects=False)
    return _walk(browser, started)


def _walk(browser: httpx.Client, start: httpx.Response) -> list[httpx.Response]:
    """Play the browser: follow the provider, then the callback it lands on."""
    assert start.status_code in (302, 307), f"{start.status_code}: {start.text[:200]}"
    state = _cookie(start, "omg_login_state")
    to_provider = _to_live(browser, start.headers["location"])
    at_provider = browser.get(to_provider, follow_redirects=False)
    assert at_provider.status_code in (302, 307), (
        f"{at_provider.status_code}: {at_provider.text[:300]}"
    )
    # The login-state cookie is `Secure`, so an http client drops it from the
    # jar. Carry it by hand: the server compares values, not transport.
    back = browser.get(
        _to_live(browser, at_provider.headers["location"]),
        cookies={"omg_login_state": state} if state else {},
        follow_redirects=False,
    )
    return [start, at_provider, back]


def _cookie(response: httpx.Response, name: str) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            # Starlette quotes cookie values that contain separators; the value
            # the server compares against has no quotes.
            return header.split(";", 1)[0].split("=", 1)[1].strip('"')
    return None


@when("a callback arrives with a state the gateway never issued",
      target_fixture="completed")
def forged_callback(browser: httpx.Client) -> list[httpx.Response]:
    # No start, so no state cookie: the gateway has nothing to compare against.
    forged = browser.get(
        "/api/auth/google/callback",
        params={"code": "made-up", "state": "never-issued"},
        follow_redirects=False,
    )
    return [forged]


def _session_cookie(response: httpx.Response) -> str | None:
    return _cookie(response, "omg_session")


def _signed_in(completed: list[httpx.Response]) -> bool:
    return any(_session_cookie(r) for r in completed)


@then("the operator has a session")
def has_session(completed: list[httpx.Response]) -> None:
    assert _signed_in(completed), (
        [r.status_code for r in completed],
        completed[-1].headers.get("location"),
    )


@then("the operator has no session")
def has_no_session(completed: list[httpx.Response]) -> None:
    assert not _signed_in(completed), [r.status_code for r in completed]


@then("the callback is refused")
def callback_refused(completed: list[httpx.Response]) -> None:
    """Refused means: no session, and a reason on the login page.

    A JSON error would leave a person staring at an API document. The redirect
    itself is the refusal; what must never happen is a session cookie.
    """
    final = completed[-1]
    assert not _signed_in([final]), "refused login still set a session"
    location = final.headers.get("location", "")
    assert final.status_code == 303, f"{final.status_code}: {final.text[:300]}"
    assert "/#/login" in location and "error=" in location, location


@then(parsers.parse('the session belongs to "{email}"'))
def session_belongs_to(container: Container, email: str,
                       completed: list[httpx.Response]) -> None:
    from gateway.interface.sessions import read_session

    token = next(_session_cookie(r) for r in completed if _session_cookie(r))
    session = read_session(token, secret=container.settings.session_secret.get_secret_value())
    assert session.email == email, session


@then("the operator was not duplicated")
def not_duplicated(container: Container, runner, google: FakeGoogle) -> None:
    operators = runner.run(container.store.count_operators())
    assert operators == 1, operators


@then(parsers.parse('"{email}" was created by the allowlist'))
def created_by_allowlist(container: Container, runner, email: str) -> None:
    """The allowlist is an address book, not a gate: it joins you to the
    installation's own tenant instead of asking which one."""
    operator_id = runner.run(container.store.fetch_operator_by_email(email))
    assert operator_id, email
    assert runner.run(
        container.read_models.operator_tenant(operator_id)
    ) == TENANT_OF_DEPLOYMENT, email

@then("the operator has no tenant")
def no_tenant(completed: list[httpx.Response], container: Container,
              runner) -> None:
    from gateway.interface.sessions import read_session

    token = next(_session_cookie(r) for r in completed if _session_cookie(r))
    session = read_session(
        token, secret=container.settings.session_secret.get_secret_value()
    )
    assert runner.run(
        container.read_models.operator_tenant(session.operator_id)
    ) is None


@when("an administrator saves the panel client for the fake provider")
def save_panel_client(container: Container, runner, google: FakeGoogle) -> None:
    async def save() -> None:
        await container.commands.configure_sign_in(
            google_client_id=f"{CLIENT_ID}-from-panel",
            google_client_secret=CLIENT_SECRET,
            redirect_uri="https://gateway.test/api/auth/google/callback",
            operator_allowlist=ALLOWED,
            actor="ops@example.dev",
        )

    runner.run(save())


@then("the redirect carries the panel's client id")
def redirect_carries_panel_client(login_start: httpx.Response) -> None:
    location = login_start.headers.get("location", "")
    assert f"client_id={CLIENT_ID}-from-panel" in location, location
