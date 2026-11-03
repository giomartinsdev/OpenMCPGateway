import { expect, test } from "@playwright/test";

// The file and the forms must be the same state machine, so this drives the
// file: declare a role and a member in YAML, apply, then look for them in the
// UI and in the export. A panel that can only be edited by hand is not
// config-as-code, whatever the page is called.
const OWNER = { email: "yaml@gateway.test", password: "yaml-password-123" };

const DOCUMENT = `roles:
  - name: Auditor
    permissions: [traffic:read, audit:read]

members:
  - email: auditor@example.dev
    password: auditor-password-123
    roles: [Auditor]

prune: false
`;

test("a role and a member declared in the file show up in the UI and the export", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("YAML Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways" }).first()).toBeVisible();

  await page.goto("/#/config");
  const editor = page.getByLabel("Gateway configuration");
  await editor.fill(DOCUMENT);

  // Validate names both sections, so a typo in the key does not read as "empty".
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.locator(".notice").first()).toContainText("roles: Auditor");
  await expect(page.locator(".notice").first()).toContainText("members: auditor@example.dev");

  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".notice").first()).toContainText("created roles: Auditor");
  await expect(page.locator(".notice").first()).toContainText(
    "created members: auditor@example.dev",
  );

  // Apply re-exports, so the file that came back has to describe what was just
  // created — otherwise the round trip is broken and nobody notices until later.
  await expect(editor).toHaveValue(/name: 'Auditor'/);
  await expect(editor).toHaveValue(/email: auditor@example\.dev/);

  await page.goto("/#/roles");
  await expect(page.getByRole("row", { name: /Auditor/ })).toBeVisible();

  await page.goto("/#/members");
  await expect(page.getByRole("row", { name: /auditor@example\.dev/ })).toBeVisible();

  // Applying the exported document again must be a no-op, not a second member.
  await page.goto("/#/config");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".notice").first()).toContainText("nothing changed");
});
