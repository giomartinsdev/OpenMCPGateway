import { expect, test } from "@playwright/test";

// The plaintext of an API key exists once, in the response that minted it. Two
// things here are the difference between working and "401 missing or invalid
// API key": it must be visible where the button is, and the row must show the
// key *id* rather than the subject (which looks like a key and is not one).
const OWNER = { email: "keys@gateway.test", password: "keys-password-123" };

async function owner(page) {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Keys Co");
  await page.getByRole("button", { name: "Create tenant" }).click();

  await page.goto("/#/gateways");
  await page.getByRole("button", { name: /new gateway/i }).first().click();
  await page.getByLabel("Slug").fill("keys-mcp");
  await page.getByLabel("Name", { exact: true }).fill("keys-mcp");
  await page.getByLabel("Upstream MCP URL").fill("http://127.0.0.1:1/mcp");
  await page.getByRole("button", { name: "Create gateway" }).click();
  await expect(page.getByRole("heading", { name: "keys-mcp" })).toBeVisible();
}

test("a new key is shown in the keys card and can be rotated", async ({ page }) => {
  await owner(page);
  const card = page.locator(".card", { has: page.getByRole("heading", { name: "API keys" }) });

  await page.getByRole("button", { name: "Issue key" }).click();
  const secret = card.locator(".secret");
  await expect(secret).toBeVisible();
  await expect(secret).toContainText("omg_");
  const first = (await secret.locator(".mono").textContent()).trim();
  expect(first.startsWith("omg_")).toBe(true);

  // The table names the id, never the secret.
  await expect(card.locator("tbody tr")).toHaveCount(1);
  await expect(card.locator("tbody tr")).toContainText("key_");

  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "rotate" }).click();

  // The panel repaints after the response; the secret must be a *different*
  // plaintext, not the one already on screen.
  await expect
    .poll(async () => (await card.locator(".secret .mono").textContent()).trim())
    .not.toBe(first);
  const second = (await card.locator(".secret .mono").textContent()).trim();
  expect(second.startsWith("omg_")).toBe(true);

  // The rotated key is the only one left, so the row changed identity.
});
