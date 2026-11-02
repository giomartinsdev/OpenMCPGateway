"""Steps for configuring admin sign-in from the panel."""

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient

scenarios("../features/admin_signin_settings.feature")

GOOD_CLIENT = "omg-client.apps.googleusercontent.com"
GOOD_SECRET = "omg-secret-value"
GOOD_CALLBACK = "https://gateway.test/api/auth/google/callback"
PASSWORD = "correct-horse-battery"


@pytest.fixture()
def browser(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


def _register(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post("/api/auth/register",
                        json={"email": email, "password": PASSWORD})


def _login(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post("/api/auth/login",
                        json={"email": email, "password": PASSWORD})


@given(parsers.parse('an administrator "{email}" with password "{password}"'))
def administrator(browser: httpx.Client, email: str, password: str) -> None:
    """The first account is the administrator; the API makes that decision."""
    assert password == PASSWORD
    _register(browser, email)
    assert _login(browser, email).status_code == 200


@given(parsers.parse('an operator "{email}" with password "{password}"'))
def plain_operator(browser: httpx.Client, email: str, password: str) -> None:
    assert password == PASSWORD
    assert _register(browser, email).status_code == 201


@when("the administrator reads the sign-in settings", target_fixture="panel")
def read_settings(browser: httpx.Client) -> dict:
    response = browser.get("/api/settings/sign-in")
    assert response.status_code == 200, response.text[:300]
    return response.json()


@when("the administrator saves a sign-in client", target_fixture="saved")
def save_client(browser: httpx.Client) -> httpx.Response:
    return browser.put("/api/settings/sign-in", json={
        "google_client_id": GOOD_CLIENT,
        "google_client_secret": GOOD_SECRET,
        "redirect_uri": GOOD_CALLBACK,
    })


@when("the administrator saves a sign-in client allowing two addresses",
      target_fixture="saved")
def save_client_with_allowlist(browser: httpx.Client) -> httpx.Response:
    return browser.put("/api/settings/sign-in", json={
        "google_client_id": GOOD_CLIENT,
        "google_client_secret": GOOD_SECRET,
        "redirect_uri": GOOD_CALLBACK,
        "operator_allowlist": ["ops@example.dev", "second@example.dev"],
    })


@when("the administrator saves the same client with no secret",
      target_fixture="saved")
def resave_without_secret(browser: httpx.Client) -> httpx.Response:
    return browser.put("/api/settings/sign-in", json={
        "google_client_id": GOOD_CLIENT,
        "google_client_secret": "",
        "redirect_uri": GOOD_CALLBACK,
    })


@when("the administrator saves a sign-in client with no callback",
      target_fixture="saved")
def save_without_callback(browser: httpx.Client) -> httpx.Response:
    return browser.put("/api/settings/sign-in", json={
        "google_client_id": GOOD_CLIENT,
        "google_client_secret": GOOD_SECRET,
        "redirect_uri": "",
    })


@then("sign-in with Google is off")
def google_off(browser: httpx.Client) -> None:
    assert browser.get("/api/auth/methods").json()["google"] is False


@then("sign-in with Google is on")
def google_on(browser: httpx.Client) -> None:
    assert browser.get("/api/auth/methods").json()["google"] is True


@then("the settings expose the callback and the client id")
def exposes_public_fields(panel: dict) -> None:
    assert panel["redirect_uri"] == GOOD_CALLBACK, panel
    assert panel["google_client_id"] == GOOD_CLIENT, panel


@then("the settings do not expose the secret")
def hides_secret(panel: dict) -> None:
    assert panel["has_google_client_secret"] is True, panel
    assert GOOD_SECRET not in str(panel), panel


@then("the allowlist holds both addresses")
def allowlist_holds(panel: dict) -> None:
    assert panel["operator_allowlist"] == [
        "ops@example.dev", "second@example.dev"
    ], panel


@then("the settings are refused with a message naming the callback")
def refused_callback(saved: httpx.Response) -> None:
    assert saved.status_code == 422, f"{saved.status_code}: {saved.text[:300]}"
    assert "redirect_uri" in saved.text, saved.text[:300]


@when("a non-administrator tries to save a sign-in client",
      target_fixture="saved")
def non_admin_saves(browser: httpx.Client) -> httpx.Response:
    assert _login(browser, "plain@example.dev").status_code == 200
    return browser.put("/api/settings/sign-in", json={
        "google_client_id": GOOD_CLIENT,
        "google_client_secret": GOOD_SECRET,
        "redirect_uri": GOOD_CALLBACK,
    })


@then("the attempt is forbidden")
def attempt_forbidden(saved: httpx.Response) -> None:
    assert saved.status_code == 403, f"{saved.status_code}: {saved.text[:300]}"
