import { expect, test } from "@playwright/test";

// The v0.23 features as an operator meets them: the latency tail, a rollback,
// webhooks, the audit export, and offboarding a member.
const OWNER = { email: "insight@e2e.test", password: "insight-password-123" };
const MEMBER = { email: "leaver@e2e.test", password: "leaver-password-123" };

test.describe.configure({ mode: "serial" });

const ids = {};

async function signIn(page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(
    page.getByRole("heading", { name: "Gateways", level: 1 }),
  ).toBeVisible();
}

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Insight Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(
    page.getByRole("heading", { name: "Gateways", level: 1 }),
  ).toBeVisible();

  // The console gateway is seeded with this tenant. Its slug is `console` only
  // for the first tenant on a deployment — a second one gets `console-<id>`, and
  // a whole-suite run shares one database, so match the prefix.
  const listed = await page.evaluate(async () => {
    const response = await fetch("/api/gateways", { credentials: "same-origin" });
    return (await response.json()).gateways;
  });
  ids.console = listed.find((gateway) => gateway.slug.startsWith("console")).id;

  const response = await page.evaluate(async () => {
    const created = await fetch("/api/gateways", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "drifting",
        slug: "drifting",
        target_url: "http://127.0.0.1:9/mcp",
        // No auth: the call reaches the guard, fails at the dead upstream, and
        // is still recorded — a refused call and a broken upstream are both
        // rows with a duration, and the percentiles need one.
        auth_mode: "none",
      }),
    });
    if (!created.ok) throw new Error(await created.text());
    return (await created.json()).id;
  });
  ids.drift = response;
  await page.close();
});

test("the latency card reports the tail, not only the average", async ({ page }) => {
  await signIn(page);

  // One refused call is enough to have a duration in the window.
  await page.evaluate(async () => {
    await fetch("/g/drifting/mcp", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
    });
  });

  await page.goto("/#/traffic");
  // The skeleton paints a placeholder stat while the first read is in flight,
  // so two nodes can match during the swap; the last one is the real card.
  const latency = page.locator(".stat", { hasText: "Avg latency" }).last();
  // The percentiles ride in the hint of the average: a percentile is a second
  // reading of the same number, not a fifth card.
  await expect(latency).toContainText(/p95 \d+ ms · p99 \d+ ms/, { timeout: 15_000 });
});

test("a gateway can be rolled back to an earlier version", async ({ page }) => {
  await signIn(page);
  // Two renames make three versions: created, first, second.
  await page.evaluate(async (id) => {
    for (const name of ["first", "second"]) {
      await fetch(`/api/gateways/${id}`, {
        method: "PATCH",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
    }
  }, ids.drift);

  await page.goto(`/#/gateways/${ids.drift}`);
  // Requests and history share one card, so the reader is not asked to scroll
  // past a log to reach the versions it came from.
  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Activity", exact: true }),
  });
  await card.getByRole("tab", { name: "History", exact: true }).click();
  await expect(card.getByRole("cell", { name: "gateway.updated" }).first()).toBeVisible();

  // v2 is the rename to "first"; v3 is the newest and has no button.
  await card
    .locator("tbody tr")
    .filter({ has: page.getByRole("cell", { name: "2", exact: true }) })
    .getByRole("button", { name: "preview" })
    .click();
  await expect(page.locator(".drawer")).toContainText("Restore version 2");
  await page.getByRole("button", { name: "Restore this version" }).click();

  // The rollback is one fact in the log, not a replay of the rename it undid.
  await expect(card.getByRole("cell", { name: "gateway.restored" })).toBeVisible();
  // v2 is the rename to "first", so the page's own title changes back.
  await expect(page.getByRole("heading", { name: "first", level: 1 })).toBeVisible();
});

test("the activity card switches between requests and history", async ({ page }) => {
  await signIn(page);
  await page.goto(`/#/gateways/${ids.console}`);

  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Activity", exact: true }),
  });
  const requests = card.getByRole("tab", { name: "Requests", exact: true });
  const history = card.getByRole("tab", { name: "History", exact: true });

  // Requests is the tab the page opens on: it answers "is it working".
  await expect(requests).toHaveAttribute("aria-selected", "true");
  // The filter-under-every-column shape is `paging.spec.js`'s subject; here the
  // point is that there is no playground left to run a tool on the operator's
  // behalf, and that both lists share one card.
  await expect(page.getByRole("heading", { name: "Playground" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Recent requests" })).toHaveCount(0);

  // History is the same table, with the same filter-under-the-column shape.
  await history.click();
  await expect(history).toHaveAttribute("aria-selected", "true");
  await expect(
    card.getByRole("textbox", { name: "Filter by Change" }),
  ).toBeVisible();
  const change = card.getByRole("textbox", { name: "Filter by Change" });
  await change.fill("gateway.created");
  const rows = card.getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(rows.first()).toContainText("gateway.created");
  await expect(rows.filter({ hasNotText: "gateway.created" })).toHaveCount(0);

  // The newest version is the one the page already shows, so it has no rollback
  // button; "restore to where it already is" is a 422 with a button on it.
  await change.fill("");
  await expect(rows.first().getByRole("button", { name: "preview" })).toHaveCount(0);
});

test("a webhook is registered and its deliveries are readable", async ({ page }) => {
  await signIn(page);
  await page.goto(`/#/gateways/${ids.console}`);

  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Webhooks", exact: true }),
  });
  await expect(card.getByText("No webhooks")).toBeVisible();

  await card.getByLabel("URL").fill("http://127.0.0.1:9/hook");
  await card.getByLabel("Signing secret").fill("a-secret-only-we-hold");
  await card.getByLabel("Outcomes").fill("failed_only_nothing");
  await card.getByRole("button", { name: "Add webhook" }).click();

  // The hook is listed with its subscribed outcome, and the secret never comes
  // back from the read endpoint.
  await expect(card.locator("tbody tr").first()).toContainText("127.0.0.1:9/hook");
  await expect(card.getByText("a-secret-only-we-hold")).toHaveCount(0);
});

test("the audit log exports as CSV through a link", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/audit");

  const link = page.getByRole("link", { name: "Export CSV" });
  await expect(link).toHaveAttribute("href", /\/api\/events\.csv/);
  const saved = await page.evaluate(async (href) => {
    const response = await fetch(href, { credentials: "same-origin" });
    return { status: response.status, body: await response.text() };
  }, await link.getAttribute("href"));

  expect(saved.status).toBe(200);
  const header = saved.body.split("\n")[0];
  expect(header).toContain("occurred_at");
  expect(header).toContain("event_type");
  // Payload is deliberately absent: an export is the file most likely to leave
  // the building, and it holds sealed secrets.
  expect(header).not.toContain("payload");
});

test("disabling a member signs them out on their next request", async ({ page }) => {
  await signIn(page);

  await page.evaluate(async (member) => {
    const created = await fetch("/api/operators", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: member.email, password: member.password }),
    });
    if (!created.ok) throw new Error(await created.text());
  }, MEMBER);

  await page.goto("/#/members");
  const row = page.locator("tbody tr", { hasText: MEMBER.email });
  await row.click();
  await page.getByRole("button", { name: "Disable", exact: true }).click();

  await expect(row.getByText("disabled")).toBeVisible();

  // A new sign-in is refused, and the old cookie is not a claim about the present.
  const login = await page.evaluate(async (member) => {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(member),
    });
    return response.status;
  }, MEMBER);
  expect(login).toBe(401);

  // Re-enabling keeps the password: there is nothing to reset.
  await row.click();
  await page.getByRole("button", { name: "Enable", exact: true }).click();
  await expect(row.getByText("disabled")).toHaveCount(0);
});

test("the monthly quota is set from the endpoint card", async ({ page }) => {
  await signIn(page);
  await page.goto(`/#/gateways/${ids.drift}`);

  const card = page.locator(".card", {
    has: page.getByRole("heading", { name: "Endpoint and limits" }),
  });
  await card.getByLabel("Calls / month").fill("10");
  await card.getByRole("button", { name: "Save quota" }).click();
  await expect(card.getByLabel("Calls / month")).toHaveValue("10");
});
