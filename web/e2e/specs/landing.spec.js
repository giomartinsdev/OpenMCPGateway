import { expect, test } from "@playwright/test";

// The landing page is the front door: it must pitch the product *and* be the
// sign-in. Two things can silently break — the hero disappearing behind the
// auth card, and the sign-in form drifting away from the one onboarding uses.
test.describe.configure({ mode: "serial" });

test("signed out, the landing page is the front door and the way in", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  await page.goto("/");

  await expect(
    page.getByRole("heading", { level: 1, name: /auth, limits and logs/i }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /three decisions/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /open the panel/i })).toBeVisible();

  // Same form, same labels: the landing embeds the sign-in rather than linking
  // to a second copy that can drift.
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByRole("link", { name: /create an account/i })).toBeVisible();

  expect(errors).toEqual([]);
});

test("the sign-in form switches to registration without leaving the page", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /create an account/i }).click();

  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^create account$/i })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
});
