import { expect, test } from "@playwright/test";

// Two features that both answer "what exactly is behind this gateway?": the
// tool allowlist (which tools it forwards) and the eye (the key it hands out).
//
// The `console` gateway seeded with the tenant is the upstream this needs — it
// points at the deployment's own `/mcp`, so the catalog is real and the key was
// minted with a sealed copy. No invented fixtures.
const OWNER = { email: "tools@gateway.test", password: "tools-password-123" };

test.describe.configure({ mode: "serial" });

async function openConsole(page) {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Tools Co");
  await page.getByRole("button", { name: "Create tenant" }).click();

  await page.goto("/#/gateways");
  // The row is the link: the gateways table has no drawer, because the gateway
  // already has a page of its own and a drawer was a second, worse copy of it.
  await page.locator("tbody tr", { hasText: "console" }).first().click();
  await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible();
}

function toolsCard(page) {
  return page.locator(".card", { has: page.getByRole("heading", { name: "Tools", exact: true }) });
}

test("the allowlist can be narrowed and widened again", async ({ page }) => {
  await openConsole(page);
  const card = toolsCard(page);

  // Empty means everything, and the badge has to say that rather than "0".
  await expect(card.getByText("Every tool the upstream serves")).toBeVisible();
  const echo = card.locator(".tools-list li", { hasText: "list_gateways" });
  await expect(echo.locator("input[type=checkbox]")).toBeChecked();

  // Ticking one box out of "all" starts from the full list: unchecking one name
  // must leave the other five, not select only that name.
  await echo.locator("input[type=checkbox]").uncheck();
  await card.getByRole("button", { name: "Save allowlist" }).click();
  await expect(card.getByText(/\d+ of \d+ tools/)).toBeVisible();
  await expect(card.getByRole("button", { name: "Allow all" })).toBeVisible();
  await expect(card.locator(".tools-list li").filter({ hasText: "traffic_stats" })
    .locator("input[type=checkbox]")).toBeChecked();

  await card.getByRole("button", { name: "Allow all" }).click();
  await expect(card.getByText("Every tool the upstream serves")).toBeVisible();
});

test("the eye reveals a key once, then it is gone", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();

  await page.goto("/#/gateways");
  // The row is the link: the gateways table has no drawer, because the gateway
  // already has a page of its own and a drawer was a second, worse copy of it.
  await page.locator("tbody tr", { hasText: "console" }).first().click();

  const card = page.locator(".card", { has: page.getByRole("heading", { name: "API keys" }) });
  const row = card.locator("tbody tr").first();
  // Never read: the row says so, and the eye is available.
  await expect(row).toContainText("never");
  const eye = row.getByRole("button", { name: "Show the key once" });
  await expect(eye).toBeEnabled();

  await eye.click();
  await expect(card.getByText("Last chance: this key cannot be shown again.")).toBeVisible();
  await expect(card.locator(".secret .mono")).toContainText("omg_");

  // One reveal is all there is: the row records who and when, and the eye dies.
  await expect(row).toContainText("seen");
  await expect(row.getByRole("button", { name: "Show the key once" })).toBeDisabled();

  // A reload proves the plaintext is gone from the server, not just the screen.
  await page.reload();
  await expect(page.locator("tbody tr").first()).toContainText("seen");
  await expect(page.locator("tbody tr").first()
    .getByRole("button", { name: "Show the key once" })).toBeDisabled();
});
