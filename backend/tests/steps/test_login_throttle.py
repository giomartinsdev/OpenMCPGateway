"""Steps for the password sign-in throttle.

Every assertion is made against the running HTTP app, because a throttle that
only exists in the command handler is a throttle a future route can skip.

The operator itself is created by the `an operator … with password …` step in
`tests/steps/conftest.py`; redefining it here would be a duplicate-step error and
is exactly the drift that file exists to prevent.
"""

import httpx
import pytest
from pytest_bdd import parsers, scenarios, then, when

scenarios("../features/login_throttle.feature")

PASSWORD = "correct-horse-battery"
WRONG = "not-the-password"

@pytest.fixture()
def browser(live_server: str) -> httpx.Client:
    client = httpx.Client(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()

@when(
    parsers.parse(
        'the caller signs in as "{email}" with a wrong password {count:d} times'
    ),
    target_fixture="attempts",
)
def fails_n_times(browser: httpx.Client, email: str, count: int) -> list[int]:
    return [
        browser.post(
            "/api/auth/login", json={"email": email, "password": WRONG}
        ).status_code
        for _ in range(count)
    ]

@when(
    parsers.parse('the caller signs in as "{email}" with a wrong password'),
    target_fixture="last_attempt",
)
def fails_again(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/login", json={"email": email, "password": WRONG}
    )

@when(
    parsers.parse('the caller signs in as "{email}" with the right password'),
    target_fixture="last_attempt",
)
def signs_in(browser: httpx.Client, email: str) -> httpx.Response:
    return browser.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )

@then(parsers.parse("the sign-in is refused with {status:d}"))
def refused_with(last_attempt: httpx.Response, status: int) -> None:
    assert last_attempt.status_code == status, (
        f"{last_attempt.status_code}: {last_attempt.text[:300]}"
    )

@then("the refusal asks to retry later")
def retry_after(last_attempt: httpx.Response) -> None:
    assert "retry-after" in {key.lower() for key in last_attempt.headers}, (
        dict(last_attempt.headers)
    )

@then("the sign-in succeeds")
def succeeds(last_attempt: httpx.Response) -> None:
    assert last_attempt.status_code == 200, (
        f"{last_attempt.status_code}: {last_attempt.text[:300]}"
    )
