// Generates `site/docs.html` from the manual the backend already ships.
//
// The text lives in `backend/src/gateway/docs/sections*.json` and is read by two
// consumers there: the console MCP's `search_docs` and (before this file) the
// panel's `#/docs` route. Publishing the same files as the public site is the
// point: one copy of the prose, and the page a stranger reads cannot describe a
// different version than the one running.
//
//   node site/build-docs.mjs
//
// All five languages are emitted into one file with the inactive ones hidden, so
// switching language is instant and the page keeps working with no network.
// That is also why the site bucket needs only one new object: every deploy
// replaces it whole.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DOCS = join(HERE, "..", "backend", "src", "gateway", "docs");
const OUT = join(HERE, "docs.html");

// Order and names match `web/src/i18n.jsx`, so the picker reads the same on both
// surfaces. The name is written in its own language on purpose.
const LANGUAGES = [
  { code: "en", name: "English", file: "sections.json" },
  { code: "pt", name: "Português", file: "sections.pt.json" },
  { code: "es", name: "Español", file: "sections.es.json" },
  { code: "zh", name: "中文", file: "sections.zh.json" },
  { code: "ru", name: "Русский", file: "sections.ru.json" },
];

const escape = (text) =>
  String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

/** The manual writes `code`, **bold** and *em*; that is the whole dialect. */
function inline(text) {
  return escape(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*]+)\*(?=[\s.,;:)]|$)/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function table({ head, rows }) {
  return `<div class="docs-table"><table>
<thead><tr>${head.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead>
<tbody>${rows
    .map(
      (row) =>
        `<tr>${row.map((cell, index) => `<td${index === 0 ? ' class="strong"' : ""}>${inline(cell)}</td>`).join("")}</tr>`,
    )
    .join("")}</tbody>
</table></div>`;
}

/** A markdown table written inline in `bodyAfter`. */
function parseTable(text) {
  const rows = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|"))
    .map((line) =>
      line
        .slice(1, -1)
        .split("|")
        .map((cell) => cell.trim()),
    );
  const [head, , ...body] = rows;
  return { head, rows: body };
}

/** One paragraph, or one fenced block: `body` items are already either-or. */
function prose(text) {
  const fence = text.match(/^```[a-z]*\n([\s\S]*?)\n?```$/);
  if (fence) {
    return `<pre class="code" tabindex="0">${escape(fence[1])}</pre>`;
  }
  return `<p>${inline(text)}</p>`;
}

function section({ id, title, body = [], table: grid, bodyAfter = [] }, code) {
  const after = bodyAfter.map((text) =>
    text.startsWith("|") ? table(parseTable(text)) : prose(text),
  );
  // The five translations share the same section ids, so the DOM id is prefixed
  // with the language: two elements with `id="auth"` would make `#auth` pick
  // whichever pane the parser reached first. `data-anchor` keeps the stable
  // name the table of contents and the landing links speak.
  return `<section class="docs-section" id="${code}-${id}" data-anchor="${id}">
<h2>${inline(title)}</h2>
${body.map(prose).join("\n")}
${grid ? table(grid) : ""}
${after.join("\n")}
</section>`;
}

function page(sections, code) {
  const toc = sections
    .map(
      (entry) =>
        `<a href="#${entry.id}" data-jump="${entry.id}">${inline(entry.title)}</a>`,
    )
    .join("\n");

  const articles = sections.map((entry) => section(entry, code)).join("\n");

  return `<div class="docs-pane" data-lang-pane hidden>
<nav class="docs-toc" aria-label="On this page">
<div class="nav-label" data-i18n="docs.onThisPage">On this page</div>
${toc}
</nav>
<article class="docs-article">
${articles}
<footer class="docs-foot">
<span>OpenMCPGateway · Apache-2.0</span>
<a href="https://github.com/giomartinsdev/OpenMCPGateway#readme" target="_blank" rel="noreferrer">README →</a>
</footer>
</article>
</div>`;
}

const panes = LANGUAGES.map((language) => {
  const payload = JSON.parse(readFileSync(join(DOCS, language.file), "utf8"));
  return page(payload.sections, language.code).replace(
    "data-lang-pane hidden",
    `data-lang-pane="${language.code}"${language.code === "en" ? "" : " hidden"}`,
  );
}).join("\n");

const options = LANGUAGES.map(
  (language) =>
    `<option value="${language.code}">${language.name}</option>`,
).join("\n");

const html = `<!doctype html>
<html lang="en" data-theme="light">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title data-i18n="docs.docTitle">The manual — OpenMCPGateway</title>
    <meta
      name="description"
      content="What OpenMCPGateway does, how a request gets through, and how to keep the configuration in a file you can review."
    />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <meta name="theme-color" content="#fbfbfc" media="(prefers-color-scheme: light)" />
    <meta name="theme-color" content="#08090c" media="(prefers-color-scheme: dark)" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
      rel="stylesheet"
    />
    <link rel="stylesheet" href="/site.css" />
    <script>
      (function () {
        try {
          var stored = localStorage.getItem("omg.site.theme");
          document.documentElement.dataset.theme =
            stored ||
            (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
        } catch (e) {}
      })();
    </script>
  </head>
  <body class="docs-page">
    <a class="skip" href="#main">Skip to content</a>

    <header class="bar">
      <div class="wrap bar-in">
        <a class="brand" href="/">
          <svg class="mark" viewBox="0 0 360 420" width="21" height="25" aria-hidden="true" focusable="false">
            <path d="M 125 130 A 55 55 0 0 1 235 130 L 235 155 L 205 155 L 205 130 A 25 25 0 0 0 155 130 L 155 155 L 125 155 Z" />
            <path d="M 152 220 L 208 220 L 208 245 C 208 268 180 282 180 282 C 180 282 152 268 152 245 Z" />
            <path fill-rule="evenodd" d="M 180 130 A 115 115 0 1 0 180 360 A 115 115 0 1 0 180 130 Z M 180 188 A 57 57 0 1 1 180 302 A 57 57 0 1 1 180 188 Z" />
          </svg>
          OpenMCP<span>Gateway</span>
        </a>

        <nav aria-label="Main">
          <a class="navlink" href="/" data-i18n="nav.home">Home</a>
          <a class="navlink" href="/#screens" data-i18n="nav.screens">Screens</a>
          <a class="navlink" href="/#run" data-i18n="nav.run">Run it</a>
          <a
            class="navlink"
            href="https://github.com/giomartinsdev/OpenMCPGateway"
            target="_blank"
            rel="noreferrer"
            data-i18n="nav.source"
            >Source</a
          >
          <div class="tools">
            <label class="lang" aria-label="Language">
              <select id="lang" aria-label="Language">
${options}
              </select>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
            </label>
            <div class="seg" role="group" aria-label="Theme">
              <button type="button" data-theme-set="light" aria-pressed="true">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" />
                </svg>
                <span data-i18n="nav.light">Light</span>
              </button>
              <button type="button" data-theme-set="dark" aria-pressed="false">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
                </svg>
                <span data-i18n="nav.dark">Dark</span>
              </button>
            </div>
            <a class="pill" href="https://mcp-gateway.giomartins.dev/" data-i18n="nav.panel">Open panel</a>
          </div>
        </nav>
      </div>
    </header>

    <main id="main" class="wrap docs-main">
      <header class="docs-head">
        <span class="eyebrow" data-i18n="nav.docs">Documentation</span>
        <h1 data-i18n="docs.heading">The manual</h1>
        <p class="sub" data-i18n="docs.sub">
          What the gateway does, how a request gets through, and how to keep the configuration
          in a file you can review. Apache-2.0, self-hosted, no account to create.
        </p>
        <div class="cta docs-cta">
          <a class="btn btn-ghost" href="https://github.com/giomartinsdev/OpenMCPGateway" target="_blank" rel="noreferrer" data-i18n="docs.source">
            Source on GitHub
          </a>
          <a class="btn btn-ghost" href="/" data-i18n="docs.back">Back to the site</a>
        </div>
      </header>

${panes}
    </main>

    <script src="/site.js" defer></script>
  </body>
</html>
`;

writeFileSync(OUT, html);
console.log(
  `docs.html: ${LANGUAGES.length} languages, ${(html.length / 1024).toFixed(0)} kB`,
);
