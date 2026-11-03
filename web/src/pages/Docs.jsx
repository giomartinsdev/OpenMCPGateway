// The manual. One page, sections anchored by id, and a sticky table of contents
// — a docs *site* with its own router would be a second app to keep in sync with
// this one, and the whole manual fits in a page.
//
// The text is not here: it comes from `GET /api/docs`, because the same copy is
// what the console MCP searches. Two copies would drift, and the one an agent
// reads would be the stale one.

import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useI18n, useT } from "../i18n.jsx";
import { Bone, Notice } from "../ui.jsx";

/** Split `code fences` out of prose so they render as blocks, not inline text. */
function Prose({ text }) {
  const parts = text.split(/```[a-z]*\n?/);
  const fenced = (text.match(/```[a-z]*\n?/g) ?? []).length;
  if (!fenced) return <Inline text={text} />;
  return parts.map((part, index) =>
    index % 2 === 1 ? (
      <pre className="code docs-code" key={index}>
        {part.replace(/\n$/, "")}
      </pre>
    ) : (
      <Inline text={part.trim()} key={index} />
    ),
  );
}

/** `inline code` inside a paragraph, without pulling in a markdown parser. */
function Inline({ text }) {
  if (!text) return null;
  const pieces = text.split(/(`[^`]+`)/g);
  return (
    <p>
      {pieces.map((piece, index) =>
        piece.startsWith("`") ? (
          <code key={index}>{piece.slice(1, -1)}</code>
        ) : (
          <span key={index}>{piece}</span>
        ),
      )}
    </p>
  );
}

function Table({ table }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {table.head.map((cell) => (
              <th key={cell}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, index) => (
                <td key={index} className={index === 0 ? "strong" : ""}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Docs({ signedIn = false }) {
  const t = useT();
  const { lang } = useI18n();
  const [sections, setSections] = useState(null);
  const [error, setError] = useState("");
  const [active, setActive] = useState("");

  // The manual is deployment content, so it is fetched per language rather than
  // translated in the browser: one copy of the prose, on the server, that the
  // console MCP searches too.
  useEffect(() => {
    let cancelled = false;
    setSections(null);
    setError("");
    api.docs(lang).then(
      (data) => {
        if (!cancelled) setSections(data.sections);
      },
      (err) => {
        if (!cancelled) setError(err.message);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [lang]);

  // Highlight the section you are reading, not the one you clicked. The
  // observer's rootMargin leaves the top third of the viewport as "current".
  useEffect(() => {
    if (!sections) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    sections.forEach((section) => {
      const node = document.getElementById(section.id);
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, [sections]);

  return (
    <div className="docs">
      <div className="docs-head">
        <span className="eyebrow">{t("Documentation")}</span>
        <h1>{t("The manual")}</h1>
        <p className="sub">
          {t("What the gateway does, how a request gets through, and how to keep the configuration in a file you can review. Apache-2.0, self-hosted, no account to create.")}
        </p>
        <div className="row" style={{ marginTop: "var(--s4)" }}>
          <a
            className="cta-secondary docs-cta"
            href="https://github.com/giomartinsdev/OpenMCPGateway"
            target="_blank"
            rel="noreferrer"
          >
            {t("Source on GitHub")}
          </a>
          {/* Signed out, `#/` is the landing page and its sign-in form; signed
              in, it is the gateway list. Same link, honest label for each. */}
          <a className="cta-secondary docs-cta" href="#/">
            {signedIn ? t("Back to the panel") : t("Sign in")}
          </a>
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      {!sections ? (
        <div className="docs-body">
          {/* The shape of the page before its text arrives: TOC column plus the
              measure. A spinner here would make the whole page jump. */}
          <nav className="docs-toc" aria-label={t("Sections")}>
            <div className="nav-label">{t("On this page")}</div>
            <Bone height={14} width="60%" />
            <Bone height={14} width="75%" />
            <Bone height={14} width="50%" />
          </nav>
          <article className="docs-article">
            <Bone height={28} width="40%" />
            <Bone height={14} />
            <Bone height={14} />
            <Bone height={14} width="80%" />
          </article>
        </div>
      ) : (
      <div className="docs-body">
        <nav className="docs-toc" aria-label={t("Sections")}>
          <div className="nav-label">{t("On this page")}</div>
          {sections.map((section) => (
            <a
              key={section.id}
              href={`#/docs`}
              onClick={(event) => {
                event.preventDefault();
                document
                  .getElementById(section.id)
                  ?.scrollIntoView({ behavior: "smooth", block: "start" });
                setActive(section.id);
              }}
              className={active === section.id ? "active" : ""}
            >
              {section.title}
            </a>
          ))}
        </nav>

        <article className="docs-article">
          {sections.map((section) => (
            <section key={section.id} id={section.id} className="docs-section">
              <h2>{section.title}</h2>
              {section.body.map((text, index) => (
                <Prose text={text} key={index} />
              ))}
              {section.table ? <Table table={section.table} /> : null}
              {(section.bodyAfter ?? []).map((text, index) =>
                text.startsWith("|") ? (
                  <Table key={index} table={parseTable(text)} />
                ) : (
                  <Prose text={text} key={index} />
                ),
              )}
            </section>
          ))}
          <footer className="docs-foot">
            <span>OpenMCPGateway · Apache-2.0</span>
            <a href="https://github.com/giomartinsdev/OpenMCPGateway#readme" target="_blank" rel="noreferrer">
              README →
            </a>
          </footer>
        </article>
      </div>
      )}
    </div>
  );
}

/** A markdown table written inline in `bodyAfter`, parsed into the same shape. */
function parseTable(text) {
  const rows = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|"))
    .map((line) =>
      line
        .slice(1, -1)
        .split("|")
        .map((cell) => cell.trim().replaceAll("`", "")),
    );
  const [head, , ...body] = rows;
  return { head, rows: body };
}
