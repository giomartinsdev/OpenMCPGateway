import { expect, test } from "@playwright/test";

// The mark is one inline SVG: the padlock O, in the accent colour. The failure
// modes worth pinning are the ones that make it a dependency on something else:
// an empty box while a chunk loads, and a mark that only exists at one size.
test("the mark is inline, sized, and needs no player", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));

  await page.goto("/");

  const mark = page.locator(".mark").first();
  await expect(mark).toBeVisible();
  // One path set, drawn now: no `mark-dot` fallback, no replaced node.
  await expect(mark.locator("path")).toHaveCount(3);
  await expect(page.locator(".mark-dot")).toHaveCount(0);

  // Sized by height, width derived from the 360×420 viewBox, so the mark is not
  // squashed by whatever box its parent happens to give it.
  const box = await mark.boundingBox();
  expect(box.height).toBeGreaterThan(16);
  expect(box.width / box.height).toBeCloseTo(360 / 420, 2);

  // Nothing loads to paint it.
  expect(requests.filter((url) => url.includes("lottie"))).toHaveLength(0);
  await context.close();
});

test("a reduced-motion reader sees the same mark", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.locator(".mark").first()).toBeVisible();
  await expect(page.locator(".mark").first().locator("path")).toHaveCount(3);
  await context.close();
});
