import { expect, test } from "@playwright/test";

// Two tenants on one deployment, and the two ways in: a code someone shares,
// and a stranger starting their own. The isolation assertions matter more than
// the forms — a shared listing here is one customer reading another's gateways.
const OWNER = { email: "owner@tenants.test", password: "owner-password-123" };
const JOINER = { email: "joiner@tenants.test", password: "joiner-password-123" };
const STRANGER = { email: "stranger@tenants.test", password: "stranger-password-123" };

test.describe.configure({ mode: "serial" });

async function register(page, account) {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await expect(page.getByRole("heading", { name: "Create a tenant" })).toBeVisible();
}

async function signIn(page, account) {
  await page.goto("/");
  // Scenarios share one browser context (the tenant is created once), so the
  // previous account has to leave before this one arrives.
  await page.evaluate(() =>
    fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }),
  );
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
}

test("signing up lands on the tenant choice, not on an empty panel", async ({ page }) => {
  await register(page, STRANGER);

  // The whole point of the change: an account with no tenant has nothing to
  // administer, and the panel says so instead of rendering pages that 403.
  await expect(page.getByRole("heading", { name: "Create a tenant" })).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);

  // Nothing is readable before the choice is made.
  const status = await page.evaluate(async () => (await fetch("/api/gateways")).status);
  expect(status).toBe(403);
});

test("the owner shares a join code and a colleague joins with it", async ({ page }) => {
  await register(page, OWNER);
  await page.getByLabel("Tenant name").fill("Joinable Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();

  await page.goto("/#/members");
  const code = (await page.locator(".endpoint span").innerText()).trim();
  expect(code).toMatch(/^[A-Z2-9]{8}$/);

  // A second person signs up — which lands them on the same choice screen,
  // not in the owner's panel — and joins with the code they were given.
  await signIn(page, OWNER); // clears the session back to the login page
  await register(page, JOINER);
  await page.getByRole("link", { name: /join with a code/i }).click();
  await page.getByLabel("Join code").fill(code.toLowerCase());
  await page.getByRole("button", { name: "Join" }).click();

  // A joiner gets the viewer role: reads the tenant, changes nothing.
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "New gateway" })).toHaveCount(0);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Members" })).toHaveCount(0);
});

test("a wrong join code is refused without saying why", async ({ page }) => {
  const account = { email: "gambler@tenants.test", password: "gambler-password-123" };
  await register(page, account);
  await page.getByRole("link", { name: /join with a code/i }).click();
  await page.getByLabel("Join code").fill("ZZZZ9999");
  await page.getByRole("button", { name: "Join" }).click();

  // Still on the choice screen, with a reason that names no tenant.
  await expect(page.getByRole("heading", { name: "Join a tenant" })).toBeVisible();
  await expect(page.locator(".notice.err")).toContainText(/join code is not valid/i);
  await expect(page.locator(".notice.err")).not.toContainText(/Joinable/i);
});
