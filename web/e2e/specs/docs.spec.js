import { expect, test } from "@playwright/test";

// The manual is a product surface, not a README mirror: it has to be reachable
// signed out and linked from the landing page — otherwise "the docs" is a page
// nobody can find. It is published by the site (`site/build-docs.mjs`), not by
// this app, so what is asserted here is the address, not the rendering.
const OWNER = { email: "docs@gateway.test", password: "docs-password-123" };

test("the landing links to the manual on the public site, and /docs redirects there", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  // The manual is published with the landing (`site/build-docs.mjs`), from the
  // same JSON the console MCP searches. This app no longer renders it: a manual
  // behind a login is a manual nobody reads before asking for access.
  await page.goto("/");
  const link = page.getByRole("link", { name: /^docs$/i }).first();
  await expect(link).toHaveAttribute("href", "https://omg.giomartins.dev/docs/");

  // The old address still works, section anchors included. Intercepted so the
  // test does not depend on the public bucket being reachable from CI.
  await page.route("https://omg.giomartins.dev/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>The manual</h1>" }),
  );
  await page.goto("/#/docs/config");
  await expect.poll(() => page.url()).toBe("https://omg.giomartins.dev/docs/#config");

  expect(errors).toEqual([]);
});

test("the bar groups navigation and survives a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: /^create account$/i }).click();
  await page.getByLabel("Tenant name").fill("Docs Co");
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByRole("heading", { name: "Gateways" }).first()).toBeVisible();

  // One icon per entry, and one mark: the logo is the SVG itself now, not a
  // box waiting for a player to fill it.
  await expect(page.locator(".nav a .icon")).toHaveCount(6);
  await expect(page.locator(".logo .mark")).toHaveCount(1);
  await expect(page.locator(".logo .mark path")).toHaveCount(3);

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
