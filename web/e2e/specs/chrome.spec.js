import { expect, test } from "@playwright/test";

// Three pieces of chrome that are easy to ship half-done: a favicon that never
// got wired, a language picker whose choice does not survive a reload, and a
// theme that repaints everything except the parts that were hard-coded.
//
// These run signed out on purpose — the landing page is the widest surface and
// the one a stranger sees first, so it is where a missing translation is most
// expensive.
test.describe.configure({ mode: "serial" });

test("the favicon is served and is the three-dot mark", async ({ page }) => {
  await page.goto("/");
  const href = await page.locator('link[rel="icon"]').getAttribute("href");
  expect(href).toBe("/favicon.svg");

  const response = await page.request.get(href);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("svg");

  const body = await response.text();
  expect(body).toContain("<circle");
  // Three dots: the mark is the same shape at 16px as it is in the topbar.
  expect(body.match(/<circle/g)).toHaveLength(3);
});

test("the landing page shows the same dot mark as the panel", async ({ page }) => {
  await page.goto("/");
  // Not the old green bar: the landing nav renders the mark component.
  await expect(page.locator(".landing-nav .mark")).toBeVisible();
  await expect(page.locator(".logo-mark")).toHaveCount(0);
});

test("the language picker translates the page and survives a reload", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /auth, limits and logs/i }),
  ).toBeVisible();

  const picker = page.getByLabel("Language");
  await picker.selectOption("pt");
  await expect(page.locator("html")).toHaveAttribute("lang", "pt");
  await expect(page.getByRole("link", { name: "Entrar" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();

  // The choice is a preference, not a session: a reload must not undo it.
  await page.reload();
  await expect(page.getByRole("link", { name: "Entrar" }).first()).toBeVisible();

  // And back, so the rest of the suite is not run in Portuguese.
  await page.getByLabel("Idioma").selectOption("en");
  await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();

  // All five are offered, each in its own language.
  const options = await page.getByLabel("Language").locator("option").allTextContents();
  expect(options).toEqual(["English", "Português", "Español", "中文", "Русский"]);
});

test("a translated language reaches the panel, not just the landing page", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Language").selectOption("es");
  await page.getByRole("link", { name: /crear una cuenta/i }).click();
  await expect(page.getByRole("heading", { name: "Crea tu cuenta" })).toBeVisible();

  await page.getByLabel("Email", { exact: true }).fill("es@gateway.test");
  await page.getByLabel("Contraseña", { exact: true }).fill("es-password-123");
  await page.getByRole("button", { name: "Crear cuenta" }).click();

  await page.getByLabel("Nombre del tenant").fill("Spanish Co");
  await page.getByRole("button", { name: "Crear tenant" }).click();

  // The signed-in chrome — the nav groups and the sign-out button — comes from
  // the same catalog, so a half-wired app is caught here.
  await expect(page.getByRole("heading", { name: "Gateways" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Salir" })).toBeVisible();

  await page.getByLabel("Idioma").selectOption("en");
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("the manual follows the language, body and all", async ({ page }) => {
  // The page chrome was already translated; the manual is deployment content, so
  // this is the assertion that the two halves agree.
  await page.goto("/#/docs");
  await expect(page.getByRole("heading", { level: 1, name: "The manual" })).toBeVisible();
  await expect(page.locator(".docs-section").first()).toContainText("MCP servers");

  await page.getByLabel("Language").selectOption("pt");
  await expect(page.getByRole("heading", { level: 1, name: "O manual" })).toBeVisible();
  await expect(page.locator(".docs-section").first()).toContainText("servidores MCP");
  // Same sections, translated: a section that failed to load would leave this
  // count short, and a stale English fetch would leave the heading wrong.
  expect(await page.locator(".docs-section").count()).toBeGreaterThanOrEqual(10);
  await expect(page.locator(".docs-toc a")).toHaveCount(
    await page.locator(".docs-section").count(),
  );

  await page.getByLabel("Idioma").selectOption("ru");
  await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("The manual");
  await expect(page.locator(".docs-section").first()).toContainText("MCP");

  await page.getByLabel("Язык").selectOption("en");
  await expect(page.getByRole("heading", { level: 1, name: "The manual" })).toBeVisible();
});

test("the theme switch flips the palette and remembers it", async ({ page }) => {
  await page.goto("/");
  const html = page.locator("html");
  await expect(html).toHaveAttribute("data-theme", "dark");

  const background = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const dark = await background();

  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(html).toHaveAttribute("data-theme", "light");
  const light = await background();
  expect(light).not.toBe(dark);

  // Light is paper, not a dimmer dark: the luminance actually crosses over.
  const luminance = (rgb) => {
    const [r, g, b] = rgb.match(/\d+/g).map(Number);
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  };
  expect(luminance(light)).toBeGreaterThan(0.8);
  expect(luminance(dark)).toBeLessThan(0.2);

  // Text has to be readable, not merely inverted: this is the failure a
  // hard-coded `color: #fafaf9` leaves behind.
  const heading = page.getByRole("heading", { level: 1 });
  const contrast = await heading.evaluate((node) => {
    const parse = (value) => value.match(/\d+/g).map(Number).slice(0, 3);
    const channel = (c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    const lum = (rgb) =>
      0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    const fg = lum(parse(getComputedStyle(node).color));
    const bg = lum(parse(getComputedStyle(document.body).backgroundColor));
    const [high, low] = [Math.max(fg, bg), Math.min(fg, bg)];
    return (high + 0.05) / (low + 0.05);
  });
  expect(contrast).toBeGreaterThan(7);

  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "light");

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(html).toHaveAttribute("data-theme", "dark");
});

test("the theme is applied before first paint, so there is no flash", async ({ page }) => {
  // The stored choice is read by the inline script in index.html. If that ever
  // moves into the bundle, the page paints dark and then flips.
  await page.addInitScript(() => localStorage.setItem("omg.theme", "light"));
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
