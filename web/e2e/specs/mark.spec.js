import { expect, test } from "@playwright/test";

// The mark is an enhancement, so the failure modes worth pinning are the ones
// that make it a dependency: an empty box before the chunk lands, and a 170 KB
// download for a reader who asked for no motion.
test("the mark shows dots before the player, and a reduced-motion reader skips it", async ({
  browser,
}) => {
  const still = await browser.newContext({ reducedMotion: "reduce" });
  const page = await still.newPage();
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));

  await page.goto("/#/docs");
  await expect(page.locator(".mark-dot").first()).toBeVisible();
  await page.waitForTimeout(1200);

  expect(requests.filter((url) => url.includes("lottie"))).toHaveLength(0);
  await expect(page.locator(".mark svg")).toHaveCount(0);
  await still.close();

  const animated = await browser.newContext();
  const moving = await animated.newPage();
  await moving.goto("/#/docs");
  // The dots are the first paint; the player replaces them in the same box.
  await expect(moving.locator(".mark svg")).toHaveCount(1, { timeout: 10_000 });
  await expect(moving.locator(".mark-dot")).toHaveCount(0);
  await animated.close();
});
