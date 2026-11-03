import { expect, test } from "@playwright/test";

// The read-side features as an operator meets them: why a call was refused, when
// a key dies, whether the upstream answers, the CSV the log exports, and the diff
// shown before an apply.
//
// Everything is asserted through the screen, not through `fetch`: a column that
// exists in the API and is not wired to a row is a column nobody has.
const OWNER = { email: "ops-view@e2e.test", password: "owner-password-123" };

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

// One tenant for the whole file: registering is the expensive step, and the
// features below do not interfere with each other.
test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Ops Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(
    page.getByRole("heading", { name: "Gateways", level: 1 }),
  ).toBeVisible();

  for (const body of [
    // api_key so a missing credential is a refusal worth explaining.
    { slug: "refused", auth_mode: "api_key" },
    { slug: "dead", target_url: "http://127.0.0.1:1/mcp" },
  ]) {
    ids[body.slug] = await page.evaluate(async (payload) => {
      const response = await fetch("/api/gateways", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: payload.slug,
          target_url: "http://127.0.0.1:9/mcp",
          ...payload,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).id;
    }, body);
  }
  await page.close();
});

test("a refused call names the reason in the drawer", async ({ page }) => {
  await signIn(page);

  const status = await page.evaluate(async () => {
    const response = await fetch("/g/refused/mcp", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
    });
    return response.status;
  });
  expect(status).toBe(401);

  await page.goto(`/#/gateways/${ids.refused}`);
  const card = page
    .locator(".card", { has: page.getByRole("heading", { name: "Activity", exact: true }) });
  const row = card.getByRole("row").filter({ has: page.getByRole("cell") }).first();
  await expect(row).toBeVisible();
  await row.click();

  const drawer = page.locator(".drawer");
  await expect(drawer).toBeVisible();
  // `outcome` says "unauthenticated"; this says which gate did it, which is the
  // difference between a 401 and a wrong key.
  await expect(drawer).toContainText("Reason");
  await expect(drawer).toContainText("missing or invalid credential");
});

test("a key can be issued with an expiry date, and never is the default", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(`/#/gateways/${ids.dead}`);

  const card = page
    .locator(".card", { has: page.getByRole("heading", { name: "API keys" }) });
  await card.getByLabel("Expires in").selectOption("30");
  await card.getByRole("button", { name: "Issue key" }).click();
  await expect(card.locator(".secret")).toContainText("omg_");
  await expect(card.getByText(/expires in 3[01] days/)).toBeVisible();

  // A key that silently expires breaks an integration nobody thought about, so
  // the default stays "never" and the column has to say so.
  await page.reload();
  await expect(card.getByText("never", { exact: true }).first()).toBeVisible();
});

test("testing the upstream reports a dead server instead of a quiet one", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(`/#/gateways/${ids.dead}`);

  await expect(page.getByText("Upstream not checked yet")).toBeVisible();
  await page.getByRole("button", { name: "Test upstream" }).click();
  await expect(page.getByText("Upstream down")).toBeVisible({ timeout: 20_000 });
});

test("the request log exports as CSV through a link", async ({ page }) => {
  await signIn(page);
  await page.goto("/#/traffic");

  const link = page.getByRole("link", { name: "Export CSV" });
  await expect(link).toHaveAttribute("href", /\/api\/requests\.csv/);
  const saved = await page.evaluate(async (href) => {
    const response = await fetch(href, { credentials: "same-origin" });
    return { status: response.status, body: await response.text() };
  }, await link.getAttribute("href"));
  expect(saved.status).toBe(200);
  expect(saved.body.split("\n")[0]).toContain("outcome");
  expect(saved.body.split("\n")[0]).toContain("reason");
});

test("previewing a config change lists it before anything is applied", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/#/config");
  await page.getByLabel("Gateway configuration").fill(`gateways:
  - slug: planned
    name: planned
    target_url: http://127.0.0.1:9/mcp
`);

  await page.getByRole("button", { name: "Preview changes" }).click();
  await expect(page.getByLabel("Planned changes")).toContainText("+ create planned");

  // Preview reads only: the gateway must not exist until Apply.
  const found = await page.evaluate(async () => {
    const response = await fetch("/api/gateways?slug=planned", {
      credentials: "same-origin",
    });
    return (await response.json()).gateways.length;
  });
  expect(found).toBe(0);
});
