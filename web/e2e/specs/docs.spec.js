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

test("the rail groups navigation and survives a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Docs Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways" }).first()).toBeVisible();

  await expect(page.locator(".nav-label")).toHaveText([
    "Gateway",
    "Traffic & Logs",
    "Settings",
    "Help",
  ]);
  // One icon per entry, and the animation got a box to live in.
  await expect(page.locator(".nav a .icon")).toHaveCount(7);
  await expect(page.locator(".mark svg")).toHaveCount(1);

  // Config as code moved under Settings, next to Members and Roles: a file and
  // the forms edit the same state, so they belong in one place.
  await expect(
    page.locator(".nav a", { hasText: "Config as code" }),
  ).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);

  // Narrow: the rail becomes a bar across the top rather than a column, and it
  // grows downwards instead of sideways — a nav that hides entries behind a
  // horizontal scroll is a nav nobody discovers.
  await page.setViewportSize({ width: 720, height: 900 });
  const bar = await page.locator(".rail").boundingBox();
  expect(bar.width).toBe(720);
  expect(bar.height).toBeLessThan(120);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  // Every entry is still on screen, not clipped out of the bar.
  for (const link of await page.locator(".nav a").all()) {
    const box = await link.boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(bar.height + 1);
  }
});
