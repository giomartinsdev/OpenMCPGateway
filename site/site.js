// The landing page has exactly three behaviours, and this is all of them:
// a theme switch, a language switch, and copy-to-clipboard on the code blocks.
//
// No framework, no build step. The page is served straight out of a bucket, so
// the less that has to happen between the file and the browser, the fewer ways
// a deploy can half-succeed.
//
// The text itself lives in `i18n/<lang>.json` as one flat map of English source
// strings to translations, mirroring `web/src/i18n/`. A key that is missing is
// left as the English already in the HTML: a half-translated page still reads,
// a blank page does not.

(() => {
  const THEME_KEY = "omg.site.theme";
  const LANG_KEY = "omg.site.lang";
  const LANGS = ["en", "pt", "es", "zh", "ru"];

  const root = document.documentElement;

  // English is the markup itself, so the first thing that happens is a snapshot
  // of it: without one, switching to Portuguese and back would leave the page in
  // Portuguese, because there is no `en.json` to restore from.
  const original = {
    text: new Map(),
    html: new Map(),
    attr: new Map(),
  };
  for (const node of document.querySelectorAll("[data-i18n]")) {
    original.text.set(node, node.textContent);
  }
  for (const node of document.querySelectorAll("[data-i18n-html]")) {
    original.html.set(node, node.innerHTML);
  }
  for (const node of document.querySelectorAll("[data-i18n-attr]")) {
    const pairs = node.dataset.i18nAttr.split(/\s+/).map((pair) => pair.split(":"));
    original.attr.set(node, pairs.map(([attribute, key]) => [attribute, node.getAttribute(attribute)]));
  }

  // -- theme --------------------------------------------------------------

  function setTheme(theme) {
    root.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Storage can be off; the switch still works for this page view.
    }
    for (const button of document.querySelectorAll("[data-theme-set]")) {
      button.setAttribute("aria-pressed", String(button.dataset.themeSet === theme));
    }
  }

  for (const button of document.querySelectorAll("[data-theme-set]")) {
    button.addEventListener("click", () => setTheme(button.dataset.themeSet));
  }
  // The inline <head> script already picked the theme, so this only syncs the
  // two buttons with it.
  setTheme(root.dataset.theme === "dark" ? "dark" : "light");

  // -- language -----------------------------------------------------------

  const picker = document.getElementById("lang");

  function storedLang() {
    try {
      return localStorage.getItem(LANG_KEY);
    } catch {
      return null;
    }
  }

  function initialLang() {
    const saved = storedLang();
    if (LANGS.includes(saved)) return saved;
    const preferred = (navigator.languages ?? [navigator.language ?? "en"])
      .map((tag) => String(tag).split("-")[0].toLowerCase())
      .find((code) => LANGS.includes(code));
    return preferred ?? "en";
  }

  function apply(lang, dictionary) {
    for (const node of document.querySelectorAll("[data-i18n]")) {
      const value = dictionary[node.dataset.i18n];
      node.textContent = typeof value === "string" ? value : original.text.get(node);
    }
    // Only the strings we author get innerHTML: they carry `<em>` and `<code>`.
    for (const node of document.querySelectorAll("[data-i18n-html]")) {
      const value = dictionary[node.dataset.i18nHtml];
      node.innerHTML = typeof value === "string" ? value : original.html.get(node);
    }
    // `data-i18n-attr="content:meta.description aria-label:nav.main"`.
    for (const node of document.querySelectorAll("[data-i18n-attr]")) {
      for (const [attribute, key] of node.dataset.i18nAttr
        .split(/\s+/)
        .map((pair) => pair.split(":"))) {
        const value = dictionary[key];
        if (!attribute) continue;
        if (typeof value === "string") node.setAttribute(attribute, value);
        else {
          const saved = (original.attr.get(node) ?? []).find(([name]) => name === attribute);
          if (saved) node.setAttribute(attribute, saved[1]);
        }
      }
    }
    root.lang = lang;
  }

  // The manual ships every language in one file (`build-docs.mjs`), so switching
  // is showing a different pane, not a request. The landing fetches its
  // dictionary instead, because five copies of the marketing copy in the HTML
  // would triple the page for a choice most readers never make.
  const panes = document.querySelectorAll("[data-lang-pane]");

  // Set by the manual section below once it knows how to re-anchor a pane; the
  // language switch calls it so a reader keeps their place instead of being
  // dropped wherever the new language happens to scroll to.
  let onPaneShown = null;

  function showPane(lang) {
    for (const pane of panes) {
      pane.hidden = pane.dataset.langPane !== lang;
    }
  }

  async function setLang(lang) {
    if (picker) picker.value = lang;
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch {
      // Ignored, same as the theme.
    }
    // The manual ships every language body in one file, so the pane switch is
    // local. Its chrome — the heading, the TOC label, the nav — is still a
    // dictionary lookup, like the landing's.
    if (panes.length) showPane(lang);
    if (panes.length && onPaneShown) onPaneShown();
    if (lang === "en") {
      // English is the text already in the markup. Restoring the snapshot is
      // what makes switching back from a translation work without a reload.
      apply("en", {});
      return;
    }
    try {
      const response = await fetch(`/i18n/${lang}.json`, { cache: "no-cache" });
      if (!response.ok) throw new Error(String(response.status));
      apply(lang, await response.json());
    } catch {
      // A missing dictionary is not worth a broken page: stay in English.
      apply("en", {});
    }
  }

  if (picker) {
    picker.addEventListener("change", () => setLang(picker.value));
  }
  const start = initialLang();
  if (start !== "en") setLang(start);
  else {
    if (picker) picker.value = "en";
    showPane("en");
  }

  // -- manual -------------------------------------------------------------

  // The table of contents and the article are the same list, so the highlight
  // follows what is being read rather than what was last clicked. Only the
  // visible pane is observed: watching all five would light two entries at once.
  const tocLinks = [...document.querySelectorAll(".docs-toc a")];
  if (tocLinks.length) {
    const visible = () => document.querySelector("[data-lang-pane]:not([hidden])");

    // A jump is the reader saying where they want to be, so it wins for the
    // moment it takes the page to settle: the observer fires on the way and
    // would otherwise relabel the link to the section just scrolled past.
    let jumping = null;

    let shown = null;
    const mark = (id) => {
      if (jumping) return;
      for (const link of tocLinks) {
        link.classList.toggle("active", link.dataset.jump === id);
      }
      if (id === shown) return;
      shown = id;
      // The address bar follows the reading position, so a copied URL lands on
      // the section it was copied from. The anchor name is the stable one, not
      // the per-language DOM id, so a link survives a language switch.
      if (history.replaceState) history.replaceState(null, "", `#${id}`);
    };

    // A jump goes straight to the section and says so in the address bar; the
    // highlight is set here, not by the observer, so it lands on the link that
    // was clicked rather than on the last section scrolled past.
    const goTo = (anchor, target) => {
      jumping = anchor;
      for (const link of tocLinks) {
        link.classList.toggle("active", link.dataset.jump === anchor);
      }
      if (history.replaceState) history.replaceState(null, "", `#${anchor}`);
      target.scrollIntoView({ block: "start" });
      setTimeout(() => {
        jumping = null;
        shown = anchor;
      }, 400);
    };

    // Which section the reader is in, asked of the document rather than of the
    // observer's entries: an entry carries the geometry of the moment it was
    // measured, and one jump measures every section in a single batch. Sorting
    // that batch by `top` picks the section being left, not the one arrived at.
    const reading = () => {
      const pane = visible();
      if (!pane) return null;
      const sections = [...pane.querySelectorAll(".docs-section")];
      // Above the first heading there is still a section being read -- the first
      // one -- so the highlight is never empty.
      let found = sections[0]?.dataset.anchor ?? null;
      for (const section of sections) {
        if (section.getBoundingClientRect().top <= 96) found = section.dataset.anchor;
      }
      return found;
    };

    document.addEventListener("click", (event) => {
      const link = event.target.closest(".docs-toc a");
      if (!link) return;
      event.preventDefault();
      const pane = document.querySelector("[data-lang-pane]:not([hidden])");
      const code = pane ? pane.dataset.langPane : "en";
      const target = document.getElementById(`${code}-${link.dataset.jump}`);
      if (!target) return;
      goTo(link.dataset.jump, target);
    });

    // Sections, not scroll events: the observer is only a "the page moved" bell,
    // and the answer comes from `reading()`.
    const observer = new IntersectionObserver(
      () => {
        const anchor = reading();
        if (anchor) mark(anchor);
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );

    const observe = () => {
      observer.disconnect();
      const pane = visible();
      if (!pane) return;
      for (const section of pane.querySelectorAll(".docs-section")) {
        observer.observe(section);
      }
    };
    observe();

    // A language switch swaps the pane, so the observer has to be pointed at the
    // new one; the picker's own handler runs first.
    if (picker) picker.addEventListener("change", observe);

    // A deep link from the landing is `/#auth`: the section id carries the
    // language prefix, so the anchor is looked up by its stable name inside the
    // pane that is actually visible.
    const jump = (hash) => {
      if (!hash) return;
      const pane = visible();
      if (!pane) return;
      const target = pane.querySelector(`[data-anchor="${hash}"]`);
      if (!target) return;
      goTo(hash, target);
    };
    jump(location.hash.slice(1));
    window.addEventListener("hashchange", () => jump(location.hash.slice(1)));
    onPaneShown = () => {
      observe();
      jump(location.hash.slice(1));
    };
  }

  // -- copy ---------------------------------------------------------------

  for (const button of document.querySelectorAll("[data-copy-target]")) {
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copyTarget);
      if (!source) return;
      const text = source.textContent;
      const label = button.querySelector("span");
      const original = label ? label.textContent : "";
      try {
        await navigator.clipboard.writeText(text);
        if (label) {
          label.textContent = label.dataset.copied ?? "Copied";
          setTimeout(() => {
            label.textContent = original;
          }, 1600);
        }
      } catch {
        // No clipboard permission: select the block so the reader can copy it
        // the way they always could.
        const range = document.createRange();
        range.selectNodeContents(source);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
    });
  }
})();
