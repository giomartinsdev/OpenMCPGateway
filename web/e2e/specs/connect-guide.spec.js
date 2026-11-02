import { expect, test } from "@playwright/test";

// The copy-paste panel. What matters: it shows a request against the real
// endpoint, and for OAuth it shows the token path before the call — the step
// order is the thing an operator gets wrong by hand.
const OWNER = { email: "guide@gateway.test", password: "guide-password-123" };

test.describe.configure({ mode: "serial" });

async function signupOwner(page) {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Guide Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();
}

async function createGateway(page, slug) {
  await page.goto("/#/gateways");
  await page.getByRole("button", { name: /new gateway/i }).first().click();
  await page.getByLabel("Slug").fill(slug);
  await page.getByLabel("Name").fill(slug);
  await page.getByLabel("Upstream MCP URL").fill("http://127.0.0.1:1/mcp");
  // The create form defaults to API key; these scenarios change it afterwards.
  await page.getByLabel("Who may call it").selectOption("none");
  await page.getByRole("button", { name: "Create gateway" }).click();
  await expect(page.getByRole("heading", { name: slug })).toBeVisible();
}

test("a public gateway shows one curl request against its endpoint", async ({ page }) => {
  await signupOwner(page);
  await createGateway(page, "public-mcp");

  const guide = page.getByRole("heading", { name: "Test this gateway" });
  await expect(guide).toBeVisible();

  const code = page.locator("pre.code").first();
  await expect(code).toContainText("/g/public-mcp/mcp");
  await expect(code).toContainText('"method": "initialize"');
  await expect(code).not.toContainText("Authorization");

  await page.getByRole("button", { name: "python" }).click();
  await expect(page.locator("pre.code").first()).toContainText("ClientSession");
});

test("an OAuth gateway shows the token flow before the call", async ({ page }) => {
  // Each test gets a fresh browser context, so the account made above has to
  // sign in again; the tenant it owns is what the gateway goes into.
  await page.goto("/");
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();

  await createGateway(page, "oauth-mcp");

  const mode = page.getByLabel("Mode");
  await mode.selectOption("oauth");
  await page.getByLabel("Identity provider").selectOption("github");
  await page.getByLabel("Client ID").fill("Ov23liTestClientId");
  await page.getByLabel("Client secret").fill("test-client-secret-value-32-bytes");
  await page.getByRole("button", { name: /^save$/i }).first().click();
  await expect(page.getByText("Auth configuration saved.")).toBeVisible();

  const steps = page.locator(".snippet");
  // Six: challenge, discovery, register, authorize, token, authenticated call.
  await expect(steps).toHaveCount(6);
  // Step 1 is the *unauthenticated* probe: its 401 starts the flow.
  await expect(steps.first()).not.toContainText("Authorization");
  await expect(page.locator("pre.code").nth(2)).toContainText("/register");
  await expect(page.locator("pre.code").nth(4)).toContainText("grant_type=authorization_code");
  await expect(steps.last()).toContainText("Bearer $ACCESS_TOKEN");
});
