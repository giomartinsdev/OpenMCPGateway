import { expect, test } from "@playwright/test";

// The manual is a product surface, not a README mirror: it has to render from
// the running build, be reachable signed out, and be linked from the landing
// page — otherwise "the docs" is a page nobody can find.
const OWNER = { email: "docs@gateway.test", password: "docs-password-123" };

test("the landing page links to the manual, and the manual renders signed out", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  await page.goto("/");
  await page.getByRole("link", { name: /^docs$/i }).first().click();

  await expect(page.getByRole("heading", { level: 1, name: "The manual" })).toBeVisible();
  // The table of contents is generated from the same list as the sections, so
  // one without the other is impossible; both being non-empty is the assertion.
  await expect(page.locator(".docs-section")).toHaveCount(9);
  await expect(page.locator(".docs-toc a")).toHaveCount(9);

  await page.goto("/#/config");
  await expect(page.getByRole("heading", { name: "The manual" })).toBeVisible();

  expect(errors).toEqual([]);
});

test("the bar groups navigation and survives a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Docs Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways" }).first()).toBeVisible();

  // One icon per entry, and the animation got a box to live in.
  await expect(page.locator(".nav a .icon")).toHaveCount(7);
  await expect(page.locator(".mark svg")).toHaveCount(1);

  // Config as code sits under Settings, next to Members and Roles: a file and
  // the forms edit the same state, so they belong in one place.
  await expect(
    page.locator(".nav a", { hasText: "Config as code" }),
  ).toBeVisible();

  // The bar is one row of chrome at the top, not a 248px column: the page gets
  // that width, and the bar never scrolls out of reach.
  const bar = await page.locator(".topbar").boundingBox();
  expect(bar.height).toBeLessThanOrEqual(60);
  expect(await page.locator(".topbar").evaluate((el) => getComputedStyle(el).position)).toBe(
    "sticky",
  );

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);

  // Narrow: the bar wraps onto a second row instead of hiding entries behind a
  // horizontal scroll — a nav whose tail is off-screen is a nav nobody finds.
  await page.setViewportSize({ width: 720, height: 900 });
  const narrow = await page.locator(".topbar").boundingBox();
  expect(narrow.width).toBe(720);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  for (const link of await page.locator(".nav a").all()) {
    const box = await link.boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(721);
    expect(box.y + box.height).toBeLessThanOrEqual(narrow.y + narrow.height + 1);
  }
});
