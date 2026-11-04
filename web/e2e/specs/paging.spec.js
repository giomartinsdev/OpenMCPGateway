import { expect, test } from "@playwright/test";

// Filters and cursors are one contract seen from two sides. The API tests prove
// the page boundaries; this proves the thing an operator actually touches — that
// typing in the box under "Name" narrows the table, and that "Load more" appends
// rather than replacing.
//
// Every assertion goes through the visible table, not through `fetch`: a filter
// that works in the API and is not wired to an input is a filter nobody has.
const OWNER = { email: "pager@e2e.test", password: "owner-password-123" };
const TENANT = "Pager Co";

test.describe.configure({ mode: "serial" });

const GATEWAYS = [
  { slug: "rancher", name: "Rancher cluster" },
  { slug: "grafana", name: "Grafana dashboards" },
  { slug: "postgres", name: "Postgres read-only" },
];

async function registerOwner(page) {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await expect(
    page.getByRole("heading", { name: "Create a tenant" }),
  ).toBeVisible();
  await page.getByLabel("Tenant name").fill(TENANT);
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(
    page.getByRole("heading", { name: "Gateways", level: 1 }),
  ).toBeVisible();
  // The console gateway's key is shown once, right where it can be copied.
  await expect(
    page.getByRole("heading", { name: "Your tenant's console gateway" }),
  ).toBeVisible();
}

/**
 * Body rows, and only the ones on screen.
 *
 * The skeleton keeps a remembered copy of the table (aria-hidden) so the next
 * load has a shape to draw. Role queries skip hidden nodes; a CSS selector does
 * not, and would count every row twice.
 */
function dataRows(page) {
  return page.getByRole("row").filter({ has: page.getByRole("cell") });
}

async function signIn(page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(
    page.getByRole("heading", { name: "Gateways", level: 1 }),
  ).toBeVisible();
}

async function createGateways(page) {
  for (const gateway of GATEWAYS) {
    await page.evaluate(async (body) => {
      const response = await fetch("/api/gateways", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, target_url: "http://127.0.0.1:9/mcp" }),
      });
      if (!response.ok) throw new Error(await response.text());
    }, gateway);
  }
}

test("a filter under a column narrows the table, and clearing it restores", async ({
  page,
}) => {
  await registerOwner(page);
  await createGateways(page);
  await page.reload();

  const rows = dataRows(page);
  // Three the scenario made, plus the console gateway every tenant ships with.
  await expect(rows).toHaveCount(4);

  // The input lives *under* the label it filters, which is what makes "Name"
  // unambiguous when three other columns are also text.
  const name = page.getByRole("textbox", { name: "Filter by Name" });
  await name.fill("graf");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Grafana dashboards");

  // A fragment, not the whole value: matching the prefix only would teach the
  // wrong search habit and hide rows that do contain it.
  await name.fill("an");
  await expect(rows).toHaveCount(2);

  // Clearing is part of filtering: a box you cannot empty is one you escape by
  // reloading. The `x` and the empty value are the same code path.
  await page.getByRole("button", { name: "Clear Name filter" }).click();
  await expect(name).toHaveValue("");
  await expect(rows).toHaveCount(4);
});

test("typing in a filter does not fire a request per keystroke", async ({ page }) => {
  await signIn(page);
  const rows = dataRows(page);
  await expect(rows.first()).toBeVisible();

  // The debounce is what keeps "cli" from asking for "c", "cl" and "cli" — and
  // from flashing "nothing matched" at the two prefixes that matched nothing.
  const asked = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/gateways")) asked.push(request.url());
  });

  await page.getByRole("textbox", { name: "Filter by Name" }).pressSequentially(
    "rancher",
    { delay: 40 },
  );
  await expect(rows).toHaveCount(1);
  expect(asked.length).toBeLessThanOrEqual(2);
});

test("clicking a column header reorders the list, and clicking again flips it", async ({
  page,
}) => {
  await signIn(page);
  const rows = dataRows(page);
  await expect(rows.first()).toBeVisible();

  // Nothing bulk exists yet: this is the four gateways the tenant starts with,
  // and their names make the two orders distinguishable by the first row.
  const header = page.getByRole("button", { name: /^Name/ });

  await header.click();
  await expect(page.getByRole("columnheader", { name: /Name/ })).toHaveAttribute(
    "aria-sort",
    "ascending",
  );
  // The header state flips before the rows do, so this waits on the data: a
  // reorder that never reached the server would keep the old first row.
  await expect(rows.first()).toContainText("Gateway console");

  await header.click();
  await expect(page.getByRole("columnheader", { name: /Name/ })).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  await expect(rows.first()).toContainText("Rancher cluster");
});

test("the id filter finds a row from a fragment of its id", async ({ page }) => {
  await signIn(page);
  const id = await page.evaluate(async () => {
    const response = await fetch("/api/gateways?slug=grafana", {
      credentials: "same-origin",
    });
    return (await response.json()).gateways[0].id;
  });

  await page.getByRole("textbox", { name: "Filter by ID" }).fill(id.slice(3, -3));
  await expect(dataRows(page)).toHaveCount(1);
  await expect(dataRows(page).first()).toContainText("Grafana dashboards");
});

test("a filter that matches nothing offers to clear itself", async ({ page }) => {
  await signIn(page);
  const rows = dataRows(page);
  await expect(rows.first()).toBeVisible();

  // The empty state is the only place the reader is told the filter — not the
  // data — is why the table is blank. Without the button it is a dead end.
  await page.getByRole("textbox", { name: "Filter by Name" }).fill("nothing-matches");
  await expect(page.getByText("No rows match this filter")).toBeVisible();
  await page.getByRole("button", { name: "Clear the filter" }).click();

  await expect(rows.first()).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Filter by Name" })).toHaveValue("");
});

test("clicking a gateway row opens the gateway's own page", async ({ page }) => {
  await signIn(page);
  await page.getByRole("row", { name: /Rancher cluster/ }).click();

  // No drawer: the gateway already has a page, and a drawer that repeated half of
  // it was a second place for the same facts to go stale.
  await expect(page.getByRole("heading", { name: "Rancher cluster", level: 1 })).toBeVisible();
  await expect(page.locator(".endpoint")).toContainText("/g/rancher/mcp");
});

test("clicking a request expands it in a drawer with the whole row", async ({ page }) => {
  await signIn(page);

  // A gateway of this tenant that requires a key, so an anonymous probe is
  // refused by the guard and recorded before any upstream is contacted. Its own
  // gateway, not the seeded `console`: another spec may own that slug.
  const refused = await page.evaluate(async () => {
    const created = await fetch("/api/gateways", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: "refusing",
        name: "Refusing",
        target_url: "http://127.0.0.1:9/mcp",
        auth_mode: "api_key",
      }),
    });
    if (!created.ok) throw new Error(await created.text());
    const response = await fetch("/g/refusing/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
    });
    return response.status;
  });
  expect(refused).toBe(401);

  await page.goto("/#/traffic");
  // A hash change does not remount the route, so the list would still be the one
  // that loaded before the probe. Recording is asynchronous: wait until the API
  // can see the row rather than reloading at a guessed moment.
  await expect
    .poll(async () =>
      page.evaluate(async () => {
        const response = await fetch("/api/requests?gateway=refusing&status=401");
        return (await response.json()).requests.length;
      }),
    )
    .toBeGreaterThan(0);
  await page.reload();

  const row = dataRows(page).filter({ hasText: "401" }).first();
  await expect(row).toBeVisible();
  await row.click();

  const drawer = page.getByRole("dialog", { name: /401 unauthenticated/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("refusing");
  await expect(drawer).toContainText("Event id");
  await expect(drawer).toContainText("before it reached the upstream server");
  // A body that was never read is said out loud rather than drawn as an empty
  // box, and the bytes that crossed the wire get a column of their own.
  await expect(drawer).toContainText("The request body was not kept for this call.");
  await expect(page.getByRole("columnheader", { name: "Size" })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Filter this gateway" })).toBeVisible();
});

test("the drawer shapes the body as JSON, and hands it over as a file", async ({ page }) => {
  await signIn(page);

  // A real call with a real body, through the console gateway: the preview is
  // stored compact, and unreadable, which is the case this formatting exists
  // for.
  const call = await page.evaluate(async () => {
    const listed = await fetch("/api/gateways", { credentials: "same-origin" });
    const gateway = (await listed.json()).gateways.find((row) =>
      row.slug.startsWith("console"),
    );
    const minted = await fetch(`/api/gateways/${gateway.id}/keys`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: "drawer" }),
    });
    if (!minted.ok) throw new Error(await minted.text());
    const { api_key: key } = await minted.json();
    const response = await fetch(`/g/${gateway.slug}/mcp`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: "2025-06-18",
          capabilities: {},
          clientInfo: { name: "e2e", version: "1" },
        },
      }),
    });
    return { slug: gateway.slug, status: response.status };
  });
  expect(call.status).toBe(200);

  await page.goto("/#/traffic");
  await expect
    .poll(async () =>
      page.evaluate(async (slug) => {
        const response = await fetch(`/api/requests?gateway=${slug}&status=200`);
        return (await response.json()).requests.length;
      }, call.slug),
    )
    .toBeGreaterThan(0);
  await page.reload();

  await dataRows(page).filter({ hasText: call.slug }).first().click();
  const drawer = page.getByRole("dialog", { name: "allowed" });
  await expect(drawer).toBeVisible();

  // Stored compact, shown one key per line: the space after the colon is the
  // formatter's fingerprint, and the raw view is one button away.
  // The last block is "What came back": the request is one line of `initialize`
  // and the response is the one worth shaping.
  const response = drawer.locator(".payload-block").last();
  const body = response.locator(".payload-body");
  await expect(body).toContainText('"jsonrpc": "');
  await response.getByRole("button", { name: "Raw" }).click();
  await expect(body).toContainText('"jsonrpc":"');
  await response.getByRole("button", { name: "Format" }).click();
  await expect(body).toContainText('"jsonrpc": "');

  const [file] = await Promise.all([
    page.waitForEvent("download"),
    response.getByRole("button", { name: "Download" }).click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/-response\.json$/);
});

test("the gateway page lists its own calls, filterable and openable", async ({ page }) => {
  await signIn(page);

  const id = await page.evaluate(async () => {
    const response = await fetch("/api/gateways?slug=refusing", {
      credentials: "same-origin",
    });
    const body = await response.json();
    return body.gateways[0].id;
  });

  await page.goto(`/#/gateways/${id}`);
  await expect(page.getByRole("heading", { name: "Refusing", level: 1 })).toBeVisible();

  // The page's own table, not a link to /traffic: same cursor, same filter under
  // every column, same drawer as every other list.
  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Activity", exact: true }),
  });
  await expect(card.getByRole("textbox", { name: "Filter by Outcome" })).toBeVisible();

  await card.getByRole("textbox", { name: "Filter by Outcome" }).fill("unauthenticated");
  const row = card.getByRole("row").filter({ has: page.getByRole("cell") }).first();
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByRole("dialog", { name: /401 unauthenticated/ })).toContainText(
    "Event id",
  );
});

test("the home page draws the request flow per gateway", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/");

  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Request flow", exact: true }),
  });
  await expect(card).toBeVisible();
  // The flow is built from the same window as the numbers above it; a gateway
  // that stopped answering an hour ago is what the window picker is for.
  await expect(card.locator(".sankey svg")).toBeVisible();
  await expect(card.locator(".sankey text").first()).toBeVisible();
});

test("clicking an audit event expands the payload in a drawer", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/audit");

  const row = dataRows(page).filter({ hasText: "tenant.created" }).first();
  await expect(row).toBeVisible();
  await row.click();

  const drawer = page.getByRole("dialog", { name: /tenant.created/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("Payload");
  await expect(drawer.locator(".payload")).toContainText("{");
});

test("the pager steps forward one page and back through the ones already seen", async ({
  page,
}) => {
  await signIn(page);

  // The page size is the hook's, and it is 50. Pushing 60 rows through the API
  // is faster than clicking a form 60 times and tests the cursor, not the form.
  const created = await page.evaluate(async () => {
    const failures = [];
    for (let index = 0; index < 60; index++) {
      const response = await fetch("/api/gateways", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: `bulk-${index}`,
          name: `Bulk ${String(index).padStart(2, "0")}`,
          target_url: "http://127.0.0.1:9/mcp",
        }),
      });
      if (!response.ok) {
        failures.push({
          index,
          status: response.status,
          body: await response.text(),
        });
      }
    }
    return failures;
  });
  expect(created).toEqual([]);

  // A hash change does not remount the route, so the list would still be the one
  // loaded before those 60 rows existed. A reload is what an operator does too.
  await page.reload();
  const rows = dataRows(page);
  await expect(rows).toHaveCount(50);

  // One page forward: the server's cursor is the only step it vouched for, so
  // that is what the bar offers. A numbered pager on a cursor list does not know
  // how many pages there are -- it only knows the ones this client has walked.
  await page.getByRole("button", { name: "Next page" }).click();
  // 60 bulk + the 5 from the previous scenarios, minus the first 50.
  await expect(rows).toHaveCount(15);
  await expect(page.getByRole("button", { name: "Next page" })).toHaveCount(0);
  await expect(page.getByText("end of list")).toBeVisible();

  // And back: page 2 is reachable because it was seen on the way here.
  await expect(page.getByRole("button", { name: "Page 1", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Page 1", exact: true }).click();
  await expect(rows).toHaveCount(50);
  await expect(page.getByRole("button", { name: "Next page" })).toBeVisible();
});

test("a filter nobody declared is refused, naming the ones that exist", async ({
  page,
}) => {
  await signIn(page);

  // The panel never sends this, so it goes through the API: what is under test
  // is that a typo is a 422 and not "every row, unfiltered".
  const detail = await page.evaluate(async () => {
    const response = await fetch("/api/gateways?outcame=allowed", {
      credentials: "same-origin",
    });
    return { status: response.status, body: await response.json() };
  });

  expect(detail.status).toBe(422);
  expect(detail.body.detail).toContain("outcame");
  expect(detail.body.detail).toContain("auth_mode");
});

test("the manual is served as data and the console token is minted once", async ({
  page,
}) => {
  // The manual itself is public; minting a credential is not, so this test signs
  // in first — a token that any visitor could mint would not be a boundary.
  await signIn(page);

  // Same text, two consumers: the public site publishes it (`site/build-docs.mjs`),
  // the console MCP searches it through this endpoint.
  const manual = await page.evaluate(async () => {
    const response = await fetch("/api/docs");
    return { status: response.status, body: await response.json() };
  });
  expect(manual.status).toBe(200);
  expect(manual.body.sections.length).toBeGreaterThanOrEqual(10);

  const minted = await page.evaluate(async () => {
    const response = await fetch("/api/mcp/token", {
      method: "POST",
      credentials: "same-origin",
    });
    return { status: response.status, body: await response.json() };
  });
  expect(minted.status).toBe(201);
  expect(minted.body.token.startsWith("omgc_")).toBe(true);
  expect(minted.body.tools).toContain("search_requests");
});

test("the request flow keeps every node and label inside its own box", async ({ page }) => {
  await signIn(page);

  // More gateways than the chart draws. Each unauthenticated POST is one logged
  // request, so this is also the shape of a real week: a dozen gateways, a few
  // of them busy. The window is only wide enough for a handful of readable rows.
  const slugs = await page.evaluate(async () => {
    const made = [];
    for (let index = 0; index < 14; index++) {
      // A slug at the validation ceiling, so the label gutter is exercised.
      const slug = `flow-${index}-${"x".repeat(30)}`.slice(0, 40);
      const created = await fetch("/api/gateways", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          name: `Flow ${index}`,
          target_url: "http://127.0.0.1:9/mcp",
        }),
      });
      if (!created.ok) throw new Error(await created.text());
      made.push(slug);
      for (let call = 0; call < 3; call++) {
        await fetch(`/g/${slug}/mcp`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json, text/event-stream",
          },
          body: JSON.stringify({ jsonrpc: "2.0", id: call, method: "tools/list" }),
        });
      }
    }
    return made;
  });
  expect(slugs).toHaveLength(14);

  await page.goto("/#/");
  await page.reload();
  // The widest window is the one with the most gateways, which is the case that
  // used to draw rows above the top of the viewBox and labels past the card.
  await page.getByRole("button", { name: "7 days" }).click();

  const box = page.locator(".card", {
    has: page.getByRole("heading", { name: "Request flow", exact: true }),
  }).locator(".sankey");
  await expect(box.locator(".sankey-node").first()).toBeVisible();
  // More gateways than rows: the tail is folded into one node rather than drawn,
  // which is what keeps the height bounded in the first place.
  await expect(box.locator("text").filter({ hasText: /\d+ others/ })).toHaveCount(1);

  const geometry = await box.evaluate((element) => {
    const svg = element.querySelector("svg");
    const [, , , height] = svg.getAttribute("viewBox").split(" ").map(Number);
    const nodes = [...svg.querySelectorAll("rect.sankey-node")];
    const frame = svg.getBoundingClientRect();
    const labels = [...svg.querySelectorAll("text")].map((text) =>
      text.getBoundingClientRect(),
    );
    return {
      height,
      top: Math.min(...nodes.map((node) => Number(node.getAttribute("y")))),
      bottom: Math.max(
        ...nodes.map(
          (node) =>
            Number(node.getAttribute("y")) + Number(node.getAttribute("height")),
        ),
      ),
      // A label wider than its gutter used to paint over the card edge, because
      // the SVG is `overflow: visible`.
      labelLeft: Math.min(...labels.map((label) => label.left)),
      labelRight: Math.max(...labels.map((label) => label.right)),
      frameLeft: frame.left,
      frameRight: frame.right,
    };
  });

  expect(geometry.top).toBeGreaterThanOrEqual(0);
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.height);
  expect(geometry.labelLeft).toBeGreaterThanOrEqual(geometry.frameLeft - 1);
  expect(geometry.labelRight).toBeLessThanOrEqual(geometry.frameRight + 1);
});
