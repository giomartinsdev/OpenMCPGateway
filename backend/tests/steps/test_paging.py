"""Steps for cursor pagination, filters, the manual and the console token.

Everything goes through the HTTP API with a real session cookie, except the last
two scenarios: a console token is a *bearer* consumed by an MCP client, so the
test calls the console tools the way an agent would — the token, the tool, and
nothing else.
"""


import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.steps.support import SecureCookieClient, owner_account

scenarios("../features/paging.feature")

OWNER = "pager@example.dev"
PASSWORD = "correct-horse-battery"


@pytest.fixture()
def owner(live_server: str) -> httpx.Client:
    client = SecureCookieClient(base_url=live_server, follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def ctx() -> dict:
    return {}


@given("the owner registered", target_fixture="account")
def owner_registered(owner: httpx.Client) -> dict:
    return owner_account(owner, OWNER)


def _create(browser: httpx.Client, slug: str, name: str) -> dict:
    response = browser.post(
        "/api/gateways",
        json={
            "slug": slug,
            "name": name,
            "target_url": "http://127.0.0.1:9/mcp",
            "description": "",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@given("the owner has 3 gateways", target_fixture="gateways")
def three_gateways(account: dict) -> list[dict]:
    browser = account["browser"]
    return [
        _create(browser, "rancher", "Rancher cluster"),
        _create(browser, "grafana", "Grafana dashboards"),
        _create(browser, "postgres", "Postgres read-only"),
    ]


# ---------------------------------------------------------------- gateways


@when(
    parsers.parse("the owner pages through the gateway list with a limit of {limit:d}"),
    target_fixture="pages",
)
def page_gateways(account: dict, limit: int) -> list[dict]:
    browser = account["browser"]
    first = browser.get(f"/api/gateways?limit={limit}")
    assert first.status_code == 200, first.text
    pages = [first.json()]
    cursor = pages[0]["next"]
    if cursor:
        second = browser.get(f"/api/gateways?limit={limit}&before={cursor}")
        assert second.status_code == 200, second.text
        pages.append(second.json())
    return pages


@then(parsers.parse("the first page has {count:d} gateways"))
def first_page_count(pages: list[dict], count: int) -> None:
    assert len(pages[0]["gateways"]) == count, pages[0]


@then("the first page offers a next cursor")
def first_page_has_cursor(pages: list[dict]) -> None:
    assert pages[0]["next"], pages[0]


@then(parsers.parse("the second page has {count:d} gateway"))
def second_page_count(pages: list[dict], count: int) -> None:
    assert len(pages) == 2, pages
    assert len(pages[1]["gateways"]) == count, pages[1]


@then("the second page offers no next cursor")
def second_page_no_cursor(pages: list[dict]) -> None:
    assert pages[1]["next"] is None, pages[1]


@then("the two pages are disjoint")
def pages_disjoint(pages: list[dict]) -> None:
    first = {gateway["id"] for gateway in pages[0]["gateways"]}
    second = {gateway["id"] for gateway in pages[1]["gateways"]}
    assert first.isdisjoint(second), (first, second)


@when(parsers.parse('the owner lists gateways filtered by name "{name}"'),
      target_fixture="listing")
def filtered_by_name(account: dict, name: str) -> dict:
    response = account["browser"].get(f"/api/gateways?name={name}")
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse("the list has {count:d} gateway"))
def list_count(listing: dict, count: int) -> None:
    assert len(listing["gateways"]) == count, listing


@then(parsers.parse('the gateway is "{slug}"'))
def listing_gateway_slug(listing: dict, slug: str) -> None:
    assert listing["gateways"][0]["slug"] == slug, listing


@when(
    parsers.parse('the owner lists gateways filtered by "{field}" "{value}"'),
    target_fixture="attempt",
)
def filtered_by_unknown(account: dict, field: str, value: str) -> httpx.Response:
    return account["browser"].get(f"/api/gateways?{field}={value}")


@then(parsers.parse('the filter is refused naming "{fragment}"'))
def filter_refused(attempt: httpx.Response, fragment: str) -> None:
    assert attempt.status_code == 422, attempt.text
    assert fragment in attempt.json()["detail"], attempt.text


@when("the owner lists gateways", target_fixture="listing")
def list_gateways(account: dict) -> dict:
    response = account["browser"].get("/api/gateways")
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse('the list includes a gateway called "{slug}"'))
def list_includes(listing: dict, slug: str) -> None:
    slugs = [gateway["slug"] for gateway in listing["gateways"]]
    assert slug in slugs, listing


@given("another tenant is created by a different account", target_fixture="second")
def another_tenant(owner: httpx.Client) -> dict:
    """A second account and its own tenant on the same deployment.

    The browser is separate on purpose: the point of the scenario is two tenants
    in one installation, and sessions are what make tenants distinct here.
    """
    other = SecureCookieClient(base_url=str(owner.base_url), follow_redirects=False)
    account = owner_account(other, "second@example.dev", "Second Co")
    try:
        yield account
    finally:
        other.close()


@then(parsers.parse('its console slug is "{prefix}" followed by its tenant id'))
def slug_names_the_tenant(second: dict) -> None:
    response = second["browser"].get("/api/gateways")
    assert response.status_code == 200, response.text
    [gateway] = [
        g for g in response.json()["gateways"] if g["slug"].startswith("console")
    ]
    expected = f"console-{second['tenant_id'].removeprefix('tn_')}"
    assert gateway["slug"] == expected, (gateway["slug"], expected)


@given("the tenant's console gateway row is removed")
def remove_console_row(account: dict, container, runner) -> None:
    """A tenant that never saw the seed: no rows at all, not a soft-deleted one.

    Both tables, because a slug is claimed in `gateway_slugs` before the gateway
    is projected: leaving that row behind is what a tenant that never ran the
    seed would *not* have, and the re-seed would then pick `console-2`.
    """
    from sqlalchemy import delete as sql_delete

    from gateway.infrastructure.db.tables import gateway_slugs, rm_gateways

    listing = account["browser"].get("/api/gateways").json()
    console = next(g for g in listing["gateways"] if g["slug"].startswith("console"))

    async def remove() -> None:
        async with container.database.session() as session, session.begin():
            await session.execute(
                sql_delete(rm_gateways).where(rm_gateways.c.id == console["id"])
            )
            await session.execute(
                sql_delete(gateway_slugs).where(
                    gateway_slugs.c.aggregate_id == console["id"]
                )
            )

    runner.run(remove())


@given("the tenant's console gateway is deleted", target_fixture="deleted")
def delete_console(account: dict) -> str:
    """Delete the seeded gateway, then let the projections catch up."""
    listing = account["browser"].get("/api/gateways").json()
    gateway = next(g for g in listing["gateways"] if g["slug"].startswith("console"))
    response = account["browser"].delete(f"/api/gateways/{gateway['id']}")
    assert response.status_code in (200, 204), response.text
    return gateway["id"]


@when("the console backfill runs", target_fixture="seeded")
def run_backfill(container, runner) -> int:
    from gateway.application.services.tenants import backfill_console_gateways

    return runner.run(backfill_console_gateways(container))


@then(parsers.parse("the gateway list counts {count:d} gateways"))
def gateway_count(account: dict, count: int) -> None:
    listing = account["browser"].get("/api/gateways").json()
    assert len(listing["gateways"]) == count, listing


@then("its target is this deployment's console")
def target_is_console(listing: dict) -> None:
    """The seeded gateway points at `/mcp` of this very deployment.

    Asserted on the path rather than the host, because the host is whatever the
    deployment calls itself; the path is what makes it the console.
    """
    [gateway] = [g for g in listing["gateways"] if g["slug"] == "console"]
    assert gateway["target_url"].endswith("/mcp"), gateway
    assert gateway["auth_mode"] == "api_key", gateway


@when("the owner lists gateways filtered by the first gateway's id",
      target_fixture="listing")
def filtered_by_id(account: dict, gateways: list[dict]) -> dict:
    gateway_id = gateways[0]["id"]
    # The middle of the id, not the whole thing: that is how someone reads it off
    # a URL or a log line.
    response = account["browser"].get(f"/api/gateways?id={gateway_id[3:-3]}")
    assert response.status_code == 200, response.text
    return response.json()


def _sorted(account: dict, order: str) -> dict:
    response = account["browser"].get(f"/api/gateways?sort=name&order={order}")
    assert response.status_code == 200, response.text
    return response.json()


@when(parsers.parse("the owner lists gateways sorted by name {order}"),
      target_fixture="listing")
def sorted_by_name(account: dict, order: str) -> dict:
    return _sorted(account, "asc" if order == "ascending" else "desc")


@then(parsers.parse("the gateway names are in {order} order"))
def names_in_order(listing: dict, order: str) -> None:
    names = [gateway["name"] for gateway in listing["gateways"]]
    assert names == sorted(names, reverse=order == "descending"), names


@given("the owner has a cursor from the gateway list sorted by name",
       target_fixture="cursor")
def cursor_from_name_sort(account: dict) -> str:
    response = account["browser"].get("/api/gateways?sort=name&order=asc&limit=1")
    assert response.status_code == 200, response.text
    assert response.json()["next"], response.text
    return response.json()["next"]


@when("the owner asks for the next page sorted by slug", target_fixture="attempt")
def next_page_other_sort(account: dict, cursor: str) -> httpx.Response:
    return account["browser"].get(f"/api/gateways?sort=slug&before={cursor}")


@when(parsers.parse('the owner lists gateways sorted by "{key}"'),
      target_fixture="attempt")
def sorted_by_unknown(account: dict, key: str) -> httpx.Response:
    return account["browser"].get(f"/api/gateways?sort={key}")


@then(parsers.parse('the sort is refused naming "{fragment}"'))
def sort_refused(attempt: httpx.Response, fragment: str) -> None:
    assert attempt.status_code == 422, attempt.text
    assert fragment in attempt.json()["detail"], attempt.text


@when("the owner lists requests sorted by duration descending",
      target_fixture="listing")
def requests_by_duration(account: dict) -> dict:
    response = account["browser"].get("/api/requests?sort=duration_ms&order=desc")
    assert response.status_code == 200, response.text
    return response.json()


@when(
    parsers.parse('the owner lists gateways with the cursor "{cursor}"'),
    target_fixture="attempt",
)
def list_with_bad_cursor(account: dict, cursor: str) -> httpx.Response:
    return account["browser"].get(f"/api/gateways?before={cursor}")


@then("the cursor is refused")
def cursor_refused(attempt: httpx.Response) -> None:
    assert attempt.status_code == 422, attempt.text


# ---------------------------------------------------------------- traffic


@given("5 requests were recorded, 2 of them refused", target_fixture="recorded")
def record_requests(gateways: list[dict], container, runner) -> list[dict]:
    """Write the tail directly.

    Going through the proxy would test the proxy (covered elsewhere) and would
    need a live upstream just to produce a row. This scenario is about the
    filter, so it seeds the projection the filter reads, in the same two steps
    the recorder takes.
    """
    from gateway.domain.events import RequestObserved

    async def write() -> None:
        for index in range(5):
            outcome = "forbidden" if index < 2 else "allowed"
            event = RequestObserved(
                aggregate_id=f"traffic_{gateways[0]['id']}",
                gateway_id=gateways[0]["id"],
                subject="someone@example.dev",
                outcome=outcome,
                status=403 if outcome == "forbidden" else 200,
                duration_ms=7,
            )
            await container.store.append_unversioned([event], actor="test")
            await container.projections.apply(0, event)

    runner.run(write())
    return []


@when(parsers.parse('the owner lists requests filtered by outcome "{outcome}"'),
      target_fixture="listing")
def requests_by_outcome(account: dict, outcome: str) -> dict:
    response = account["browser"].get(f"/api/requests?outcome={outcome}")
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse("the request list has {count:d} rows"))
def request_list_count(listing: dict, count: int) -> None:
    assert len(listing["requests"]) == count, listing


@when("the owner lists requests of the first gateway by its id",
      target_fixture="listing")
def requests_of_one_gateway(account: dict, gateways: list[dict]) -> dict:
    response = account["browser"].get(f"/api/requests?gateway_id={gateways[0]['id']}")
    assert response.status_code == 200, response.text
    return response.json()


@then("every row belongs to that gateway")
def every_row_is_of_the_gateway(listing: dict, gateways: list[dict]) -> None:
    assert {row["gateway_id"] for row in listing["requests"]} == {gateways[0]["id"]}, listing


@then(parsers.parse('every row is "{value}"'))
def every_row_is(listing: dict, value: str) -> None:
    assert {row["outcome"] for row in listing["requests"]} == {value}, listing


# ------------------------------------------------------------------ audit


@when(parsers.parse('the owner lists events filtered by type "{kind}"'),
      target_fixture="listing")
def events_by_type(account: dict, kind: str) -> dict:
    response = account["browser"].get(f"/api/events?type={kind}")
    assert response.status_code == 200, response.text
    return response.json()


@then(parsers.parse("the event list has {count:d} rows"))
def event_list_count(listing: dict, count: int) -> None:
    assert len(listing["events"]) == count, listing


@then(parsers.parse("the event list has at least {count:d} rows"))
def event_list_at_least(listing: dict, count: int) -> None:
    assert len(listing["events"]) >= count, listing


@then(parsers.parse('every event is a "{kind}"'))
def every_event_is(listing: dict, kind: str) -> None:
    assert {event["event_type"] for event in listing["events"]} == {kind}, listing


# ---------------------------------------------------------------- members


@given("3 members were added")
def add_members(account: dict) -> None:
    browser = account["browser"]
    for index in range(3):
        response = browser.post(
            "/api/operators",
            json={
                "email": f"member{index}@example.dev",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 201, response.text


@when(
    parsers.parse("the owner pages through the member list with a limit of {limit:d}"),
    target_fixture="pages",
)
def page_members(account: dict, limit: int) -> list[dict]:
    response = account["browser"].get(f"/api/operators?limit={limit}")
    assert response.status_code == 200, response.text
    return [response.json()]


@then(parsers.parse("the first member page has {count:d} members"))
def member_page_count(pages: list[dict], count: int) -> None:
    assert len(pages[0]["operators"]) == count, pages[0]


@then("the member page offers a next cursor")
def member_page_cursor(pages: list[dict]) -> None:
    assert pages[0]["next"], pages[0]


# ---------------------------------------------------------------- console


@when("the owner mints a console token", target_fixture="console_token")
def mint_token(account: dict) -> dict:
    response = account["browser"].post("/api/mcp/token")
    assert response.status_code == 201, response.text
    return response.json()


@then("the console token is returned once, with an expiry")
def token_has_expiry(console_token: dict) -> None:
    assert console_token["token"].startswith("omgc_"), console_token
    assert console_token["expires_in"] > 0, console_token
    assert console_token["endpoint"].endswith("/mcp"), console_token


@then("the token can list the gateways")
def token_verifies(console_token: dict, container, runner) -> None:
    """The verifier turns the token into the operator it was minted for."""
    verifier = container.console_verifier

    async def check():
        return await verifier.verify_token(console_token["token"])

    access = runner.run(check())
    assert access is not None, access
    assert access.subject == console_token["operator_id"], access


@then("the token cannot create a gateway")
def token_cannot_write(container, runner) -> None:
    """No write tool exists — the assertion is the tool list itself.

    Read-only here is structural, not a permission check: nothing in the console
    package imports a command, so a future tool cannot quietly gain a write.
    """
    async def names() -> set[str]:
        return {tool.name for tool in await container.console.list_tools()}

    assert runner.run(names()) == {
        "search_docs",
        "list_gateways",
        "get_gateway",
        "search_requests",
        "search_events",
        "traffic_stats",
    }

@when("an MCP client lists tools over HTTP with that token",
      target_fixture="console_listing")
def console_over_http(console_token: dict, live_server: str) -> dict:
    """The same handshake a real client does, against `/mcp`.

    In-process `list_tools()` proves the registry; only an HTTP round trip proves
    the ASGI app was the one whose lifespan ran. The endpoint the API reports is
    the deployment's public URL, so this swaps in the test server's origin.
    """
    endpoint = f"{live_server}/mcp"
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {console_token['token']}",
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers=headers,
        )
        assert response.status_code == 200, f"{response.status_code}: {response.text[:400]}"
        headers["mcp-session-id"] = response.headers.get("mcp-session-id", "")
        client.post(
            endpoint,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        listed = client.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
    assert listed.status_code == 200, f"{listed.status_code}: {listed.text[:400]}"
    return {"status": listed.status_code, "body": listed.text}

@then("the console answers with its tool list")
def console_http_body(console_listing: dict) -> None:
    assert "list_gateways" in console_listing["body"], console_listing["body"][:400]


# ------------------------------------------------------------------- docs


@when("anyone reads the manual", target_fixture="manual")
def read_manual(owner: httpx.Client) -> dict:
    response = owner.get("/api/docs")
    assert response.status_code == 200, response.text
    return response.json()

@when(parsers.parse('anyone reads the manual in "{language}"'), target_fixture="manual")
def read_manual_in(owner: httpx.Client, language: str) -> dict:
    response = owner.get("/api/docs", params={"lang": language})
    assert response.status_code == 200, response.text
    return response.json()

@then(parsers.parse('the manual is served in "{language}"'))
def manual_language(manual: dict, language: str) -> None:
    assert manual["lang"] == language, manual["lang"]

@then("the manual is not the English one")
def manual_differs(owner: httpx.Client, manual: dict) -> None:
    english = owner.get("/api/docs").json()["sections"]
    assert [s["title"] for s in manual["sections"]] != [s["title"] for s in english]
    # Same shape, so a translated manual cannot lose a section or a table.
    assert [s["id"] for s in manual["sections"]] == [s["id"] for s in english]
    assert all(
        len(a.get("body") or []) == len(b.get("body") or [])
        for a, b in zip(manual["sections"], english, strict=True)
    )

@then(parsers.parse('the console searches the "{language}" manual for "{query}"'))
def manual_searchable_in(language: str, query: str) -> None:
    from gateway.docs_content import search

    assert search(query, lang=language), query


@then(parsers.parse("the manual has at least {count:d} sections"))
def manual_sections(manual: dict, count: int) -> None:
    assert len(manual["sections"]) >= count, manual


@then(parsers.parse('the console searches the manual for "{query}"'))
def manual_searchable(query: str) -> None:
    from gateway.docs_content import search

    assert search(query), query


@when("anyone presents a console token signed with another secret",
      target_fixture="attempt")
def foreign_token() -> dict:
    from gateway.console.tokens import mint

    return {
        "token": mint(
            secret="a-completely-different-secret-32-bytes!!",
            operator_id="op_foreign",
            email="intruder@example.dev",
        )
    }


@then("the console refuses the request")
def console_refuses(attempt: dict, container, runner) -> None:
    async def verify():
        return await container.console_verifier.verify_token(attempt["token"])

    assert runner.run(verify()) is None
