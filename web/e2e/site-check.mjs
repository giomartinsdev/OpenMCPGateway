// The public page is plain HTML/CSS, so there is no build to fail — but there
// are four things that break it silently: a missing asset, a horizontal
// scrollbar, a broken heading outline, and a colour pair that no longer clears
// AA once a token moves.
//
//   node e2e/site-check.mjs http://127.0.0.1:8200   # from web/
//
// It is a check, not a test framework: asserts, one file, exit code.
import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const BASE = process.argv[2] ?? "http://127.0.0.1:8200";
const browser = await chromium.launch();

// Both palettes, because the page has no toggle: it follows the system, and the
// light values are the ones a token change breaks first.
for (const [label, viewport, colorScheme] of [
  ["desktop dark", { width: 1440, height: 900 }, "dark"],
  ["desktop light", { width: 1440, height: 900 }, "light"],
  ["mobile dark", { width: 390, height: 844 }, "dark"],
]) {
  const page = await browser.newPage({ viewport, colorScheme, baseURL: BASE });
  const failures = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(message.text());
  });
  page.on("requestfailed", (request) => failures.push(`404? ${request.url()}`));

  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  // `loading="lazy"` images below the fold never load without a scroll, and a
  // broken one would go unnoticed. Force them eager and wait for the decode.
  await page.evaluate(async () => {
    await Promise.all(
      [...document.images].map(async (image) => {
        image.loading = "eager";
        image.src = image.src; // eslint-disable-line no-self-assign
        try {
          await image.decode();
        } catch {
          /* a failure surfaces as naturalWidth 0 below */
        }
      }),
    );
  });

  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    images: [...document.images].map((image) => ({
      src: image.currentSrc,
      ok: image.complete && image.naturalWidth > 0,
    })),
    headings: [...document.querySelectorAll("h1, h2, h3")].map((node) => node.tagName),
    links: [...document.querySelectorAll("a")].length,
  }));

  // One h1, and no level skipped below it: a screen reader's outline is the
  // only navigation this page has.
  assert.equal(layout.headings.filter((tag) => tag === "H1").length, 1, label);
  let previous = 1;
  for (const tag of layout.headings) {
    const level = Number(tag.slice(1));
    assert.ok(level <= previous + 1, `${label}: skipped from h${previous} to ${tag}`);
    previous = level;
  }
  assert.deepEqual(layout.overflow, 0, `${label}: page scrolls sideways`);
  const broken = layout.images.filter((image) => !image.ok);
  assert.deepEqual(broken, [], `${label}: broken images`);

  // Text that no longer clears AA is the failure a palette change causes, and
  // the one nobody notices on their own monitor.
  const contrast = await page.evaluate(() => {
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((value) => {
        const channel = value / 255;
        return channel <= 0.03928
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const parse = (value) => value.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number);
    const background = (node) => {
      for (let current = node; current; current = current.parentElement) {
        const colour = getComputedStyle(current).backgroundColor;
        if (colour && !colour.endsWith(", 0)") && colour !== "transparent") {
          return parse(colour);
        }
      }
      return [15, 14, 13];
    };
    const samples = [];
    for (const node of document.querySelectorAll("p, li, h1, h2, h3, a, span, code")) {
      if (!node.textContent.trim() || node.getAttribute("aria-hidden") === "true") continue;
      if (node.querySelector("p, h1, h2, h3")) continue;
      const style = getComputedStyle(node);
      if (style.visibility === "hidden" || style.display === "none") continue;
      const size = parseFloat(style.fontSize);
      const weight = Number(style.fontWeight) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const fg = lum(parse(style.color));
      const bg = lum(background(node));
      const ratio = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      samples.push({ text: node.textContent.trim().slice(0, 40), ratio, floor: large ? 3 : 4.5 });
    }
    return samples.filter((sample) => sample.ratio < sample.floor);
  });
  assert.deepEqual(contrast, [], `${label}: below AA`);

  assert.deepEqual(failures, [], `${label}: console/network errors`);
  console.log(`  ${label}: ok · ${layout.links} links · ${layout.images.length} images`);
  await page.close();
}

await browser.close();
console.log("ok");
