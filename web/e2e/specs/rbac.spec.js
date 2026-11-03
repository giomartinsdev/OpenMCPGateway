import { expect, test } from "@playwright/test";

// The tenant is created by the first account in this file, so every scenario
// shares one database and runs in order (`workers: 1`). Splitting this into
// independent files would need a tenant per file, and the point here is the
// sequence an administrator actually walks: bootstrap, define a role, invite
// someone, watch their access change.
const OWNER = { email: "owner@e2e.test", password: "owner-password-123" };
const MEMBER = { email: "member@e2e.test", password: "member-password-123" };
const TENANT = "E2E Co";

test.describe.configure({ mode: "serial" });

async function registerOwner(page) {
  // Signing up grants an identity and nothing else; the tenant is the next
  // screen, and choosing to create one is what makes this account its owner.
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await expect(page.getByRole("heading", { name: "Create a tenant" })).toBeVisible();
  await page.getByLabel("Tenant name").fill(TENANT);
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();
}

async function signIn(page, account) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await expect(page.getByRole("heading", { name: "Gateways", level: 1 })).toBeVisible();
}

test("the account that creates a tenant owns it and sees every screen", async ({ page }) => {
  await registerOwner(page);

  const nav = page.getByRole("navigation");
  for (const label of ["Gateways", "Traffic", "Audit log", "Config as code", "Members", "Roles"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  await expect(page.locator(".who")).toContainText("owner");
  await expect(page.locator(".who")).toContainText(TENANT);
});

test("an administrator defines a role by ticking permissions", async ({ page }) => {
  await signIn(page, OWNER);

  await page.goto("/#/roles");
  await page.getByRole("button", { name: "New role" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Auditor");
  await page.locator(".permission", { hasText: "traffic:read" }).getByRole("checkbox").check();
  await page.locator(".permission", { hasText: "audit:read" }).getByRole("checkbox").check();
  await page.getByRole("button", { name: "Save role" }).click();

  const row = page.getByRole("row", { name: /Auditor/ });
  await expect(row).toContainText("traffic:read");
  await expect(row).toContainText("audit:read");
  // Two built-in roles came with the deployment, and neither offers a delete.
  const owner = page.getByRole("row", { name: /role_owner/ });
  await expect(owner).toContainText("built-in");
  await expect(owner.getByRole("button", { name: "Delete" })).toHaveCount(0);
});

test("a permission outside the catalog is refused, naming it", async ({ page }) => {
  await signIn(page, OWNER);
  await page.goto("/#/roles");

  // Through the API, because the panel only offers checkboxes: the validation
  // has to live on the server, and this is the assertion that says so.
  const detail = await page.evaluate(async () => {
    const response = await fetch("/api/roles", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Typo", permissions: ["traffic:reed"] }),
    });
    return { status: response.status, body: await response.json() };
  });

  expect(detail.status).toBe(422);
  expect(detail.body.detail).toContain("traffic:reed");
});

test("a member added without a role gets the viewer role", async ({ page }) => {
  await signIn(page, OWNER);
  await page.goto("/#/members");

  await page.getByRole("button", { name: "Add member" }).click();
  const form = page.getByRole("dialog", { name: "Add a member" });
  await form.getByLabel("Email", { exact: true }).fill(MEMBER.email);
  await form.getByLabel("Initial password").fill(MEMBER.password);
  await form.getByRole("button", { name: "Save member" }).click();

  const row = page.getByRole("row", { name: new RegExp(MEMBER.email) });
  await expect(row).toContainText("viewer");
});

test.describe("a member", () => {
  test("reads traffic but is offered no write action", async ({ page }) => {
    await signIn(page, MEMBER);

    // Reads all arrive; the entries that would render a 403 do not.
    const nav = page.getByRole("navigation");
    await expect(nav.getByRole("link", { name: "Traffic" })).toBeVisible();
    for (const hidden of ["Members", "Roles"]) {
      await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: "New gateway" })).toHaveCount(0);

    await page.goto("/#/traffic");
    await expect(page.getByRole("heading", { name: "Traffic", level: 1 })).toBeVisible();

    // A hidden button is not a rule: the same action through the API is refused.
    const status = await page.evaluate(async () => {
      const response = await fetch("/api/gateways", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: "by-member",
          name: "By member",
          target_url: "http://upstream.test/mcp",
        }),
      });
      return response.status;
    });
    expect(status).toBe(403);
  });
});

test("granting a role takes effect on the member's next click, without signing in again", async ({ browser }) => {
  // Two browsers: the owner grants, the member keeps the session it already has.
  const owner = await browser.newContext();
  const member = await browser.newContext();
  const ownerPage = await owner.newPage();
  const memberPage = await member.newPage();

  await signIn(ownerPage, OWNER);
  await signIn(memberPage, MEMBER);

  // A role with exactly the one permission under test, created through the UI
  // so this covers the form as well as the effect.
  await ownerPage.goto("/#/roles");
  await ownerPage.getByRole("button", { name: "New role" }).click();
  await ownerPage.getByLabel("Name", { exact: true }).fill("Gateway editor");
  await ownerPage.locator(".permission", { hasText: "gateways:read" }).getByRole("checkbox").check();
  await ownerPage.locator(".permission", { hasText: "gateways:write" }).getByRole("checkbox").check();
  await ownerPage.getByRole("button", { name: "Save role" }).click();
  await expect(ownerPage.getByRole("row", { name: /Gateway editor/ })).toBeVisible();

  await ownerPage.goto("/#/members");
  await ownerPage
    .getByRole("row", { name: new RegExp(MEMBER.email) })
    .getByRole("button", { name: "Change roles" })
    .click();
  const editorCard = ownerPage.getByRole("dialog", { name: `Roles for ${MEMBER.email}` });
  await editorCard.locator(".permission", { hasText: "Gateway editor" }).getByRole("checkbox").check();
  await editorCard.locator(".permission", { hasText: "viewer" }).getByRole("checkbox").uncheck();
  await editorCard.getByRole("button", { name: "Save roles" }).click();
  await expect(ownerPage.getByRole("row", { name: new RegExp(MEMBER.email) })).toContainText(
    "Gateway editor",
  );

  // The member's cookie was issued before the change and is untouched. A reload
  // is the "next click": the server resolves the role per request.
  await memberPage.reload();
  await expect(memberPage.getByRole("button", { name: "New gateway" })).toBeVisible();

  await memberPage.getByRole("button", { name: "New gateway" }).click();
  await memberPage.getByLabel("Name", { exact: true }).fill("Member's gateway");
  await memberPage.getByLabel("Slug").fill("by-member");
  await memberPage.getByLabel("Upstream MCP URL").fill("http://upstream.test/mcp");
  await memberPage.getByRole("button", { name: "Create gateway" }).click();
  await expect(memberPage.getByRole("heading", { name: "Member's gateway", level: 1 })).toBeVisible();

  // Revoking it is the same path in reverse: the button goes away, and so does
  // the request it would have made.
  await ownerPage.reload();
  await ownerPage
    .getByRole("row", { name: new RegExp(MEMBER.email) })
    .getByRole("button", { name: "Change roles" })
    .click();
  const editorDrawer = ownerPage.getByRole("dialog", {
    name: `Roles for ${MEMBER.email}`,
  });
  await editorDrawer
    .locator(".permission", { hasText: "Gateway editor" })
    .getByRole("checkbox")
    .uncheck();
  await editorDrawer.getByRole("button", { name: "Save roles" }).click();

  await memberPage.reload();
  await expect(memberPage.getByRole("button", { name: "New gateway" })).toHaveCount(0);

  await owner.close();
  await member.close();
});
