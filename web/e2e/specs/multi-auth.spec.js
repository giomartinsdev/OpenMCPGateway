import { expect, test } from "@playwright/test";

// One gateway, several ways in. The form is the whole feature's surface: a
// checkbox that adds keys beside the login, and a repeatable row per extra
// issuer. What matters here is that the choice survives a save and is visible
// afterwards — an operator who cannot tell what a gateway accepts will open a
// second gateway "just in case", which is the duplication this removes.
const OWNER = { email: "multi@gateway.test", password: "multi-password-123" };

test.describe.configure({ mode: "serial" });

async function owner(page, { create = false } = {}) {
  await page.goto("/");
  if (create) {
    await page.getByRole("link", { name: /create an account/i }).click();
    await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
    await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
    await page.getByRole("button", { name: /^create account$/i }).click();
    await page.getByLabel("Tenant name").fill("Multi Co");
    await page.getByRole("button", { name: "Create tenant" }).click();
  } else {
    // The first test creates the account and its tenant; this one signs in to
    // the same one, which `mode: "serial"` guarantees already exists.
    await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
    await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
    await page.getByRole("button", { name: /^sign in$/i }).click();
  }
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();
}

test("a gateway accepts a key and a second issuer beside its login", async ({ page }) => {
  await owner(page, { create: true });
  await page.goto("/#/gateways");
  await page.getByRole("button", { name: /new gateway/i }).first().click();
  await page.getByLabel("Slug").fill("multi-mcp");
  await page.getByLabel("Name", { exact: true }).fill("multi-mcp");
  await page.getByLabel("Upstream MCP URL").fill("http://127.0.0.1:1/mcp");
  await page.getByLabel("Who may call it").selectOption("none");
  await page.getByRole("button", { name: "Create gateway" }).click();
  await expect(page.getByRole("heading", { name: "multi-mcp" })).toBeVisible();

  // A key has to exist before the gateway may promise to accept one: the domain
  // refuses the combination otherwise, and the form says so.
  await page.getByRole("button", { name: "Issue key" }).click();
  await expect(page.locator(".card", { has: page.getByRole("heading", { name: "API keys" }) })
    .locator("tbody tr")).toHaveCount(1);

  await page.getByLabel("Mode").selectOption("oauth");
  await page.getByLabel("Identity provider").selectOption("github");
  await page.getByLabel("Client ID").fill("Ov23liMultiClientId");
  await page.getByLabel("Client secret").fill("multi-client-secret-value-32-bytes");
  await page.getByLabel("Also accept API keys on this gateway").check();

  await page.getByRole("button", { name: "Add issuer" }).click();
  await page.getByLabel("label").fill("corp");
  await page.getByLabel("Issuer").fill("https://login.example.com/");
  await page.getByLabel("Audience").fill("omg");
  await page.getByLabel("JWKS").fill("https://login.example.com/.well-known/jwks.json");

  await page.getByRole("button", { name: "Save auth" }).click();
  await expect(page.getByText("Auth configuration saved.")).toBeVisible();

  // The header badge is the answer to "what does this URL accept", without
  // opening the form again.
  await expect(page.locator(".card", { has: page.getByRole("heading", { name: "Auth" }) }))
    .toContainText("+ keys");
  await expect(page.getByText("+ 1 issuer")).toBeVisible();

  // And it survives a reload, which is the part that would silently drop if the
  // form mapped the fields but the API did not store them.
  await page.reload();
  await expect(page.getByLabel("Also accept API keys on this gateway")).toBeChecked();
  await expect(page.getByLabel("Issuer")).toHaveValue("https://login.example.com/");
});

test("an issuer row can be removed before saving", async ({ page }) => {
  await owner(page);
  await page.goto("/#/gateways");
  await page.getByRole("button", { name: /new gateway/i }).first().click();
  await page.getByLabel("Slug").fill("prune-mcp");
  await page.getByLabel("Name", { exact: true }).fill("prune-mcp");
  await page.getByLabel("Upstream MCP URL").fill("http://127.0.0.1:1/mcp");
  await page.getByLabel("Who may call it").selectOption("none");
  await page.getByRole("button", { name: "Create gateway" }).click();
  await expect(page.getByRole("heading", { name: "prune-mcp" })).toBeVisible();

  await page.getByLabel("Mode").selectOption("oauth");
  await page.getByLabel("Identity provider").selectOption("github");
  await page.getByLabel("Client ID").fill("Ov23liPruneClientId");
  await page.getByLabel("Client secret").fill("prune-client-secret-value-32-bytes");

  await page.getByRole("button", { name: "Add issuer" }).click();
  await expect(page.getByLabel("Issuer")).toBeVisible();
  // A half-filled row must not be submitted as an issuer with empty fields.
  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByLabel("Issuer")).toHaveCount(0);

  await page.getByRole("button", { name: "Save auth" }).click();
  await expect(page.getByText("Auth configuration saved.")).toBeVisible();
});
