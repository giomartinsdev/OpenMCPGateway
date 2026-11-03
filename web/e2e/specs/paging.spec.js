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
  await expect(rows).toHaveCount(3);

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

  await name.fill("");
  await expect(rows).toHaveCount(3);
});

test("load more appends the next page instead of replacing the list", async ({
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

  await page.getByRole("button", { name: "Load more" }).click();
  // 60 bulk + the 3 from the previous scenario: one more page, appended.
  await expect(rows).toHaveCount(63);
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
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
  await page.goto("/#/docs");
  await expect(
    page.getByRole("heading", { level: 1, name: "The manual" }),
  ).toBeVisible();

  // Same text, two consumers: the panel renders it, the console MCP searches it.
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
