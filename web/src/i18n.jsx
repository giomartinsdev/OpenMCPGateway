// Five languages, no dependency.
//
// The key *is* the English sentence. That makes the call sites readable
// (`t("Sign in")` beats `t("nav.signin")`), removes a second vocabulary to keep
// in sync, and gives a free fallback: a string nobody translated renders in
// English instead of blank. Translators only ever see the pairs.
//
// `{name}` is the whole interpolation language. Plural forms are not magic:
// the code picks the sentence (`1 route` / `3 routes`), and each locale
// translates both, because Slavic and Chinese plural rules do not fit one
// suffix rule and guessing them is how "3 route" ships.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import pt from "./i18n/pt.js";
import es from "./i18n/es.js";
import zh from "./i18n/zh.js";
import ru from "./i18n/ru.js";

const KEY = "omg.lang";

// `name` is written in its own language on purpose: someone looking for Russian
// scans for "Русский", not for "Russian".
export const LANGUAGES = [
  { code: "en", name: "English", locale: "en-US" },
  { code: "pt", name: "Português", locale: "pt-BR" },
  { code: "es", name: "Español", locale: "es-ES" },
  { code: "zh", name: "中文", locale: "zh-CN" },
  { code: "ru", name: "Русский", locale: "ru-RU" },
];

const DICTIONARIES = { en: {}, pt, es, zh, ru };
const CODES = new Set(LANGUAGES.map((language) => language.code));

/** Stored choice, else the browser's, else English. */
function initial() {
  try {
    const stored = localStorage.getItem(KEY);
    if (CODES.has(stored)) return stored;
  } catch {
    // Storage can be off; the browser's language still works.
  }
  const preferred = (navigator.languages ?? [navigator.language ?? "en"]).find((tag) =>
    CODES.has(String(tag).split("-")[0].toLowerCase()),
  );
  return preferred ? String(preferred).split("-")[0].toLowerCase() : "en";
}

function interpolate(template, values) {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name) =>
    name in values ? String(values[name]) : whole,
  );
}

const I18n = createContext(null);

// Read by `relative()` in ui.jsx, which formats dates outside React. A React
// value cannot cross that boundary without threading a prop through every row.
let activeLocale = "en-US";

export function getLocale() {
  return activeLocale;
}

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(initial);

  useEffect(() => {
    const language = LANGUAGES.find((entry) => entry.code === lang) ?? LANGUAGES[0];
    activeLocale = language.locale;
    document.documentElement.lang = lang;
    try {
      localStorage.setItem(KEY, lang);
    } catch {
      // Remembering the language is a convenience, not a requirement.
    }
  }, [lang]);

  const t = useCallback(
    (text, values) => interpolate(DICTIONARIES[lang]?.[text] ?? text, values),
    [lang],
  );

  const value = useMemo(() => {
    const language = LANGUAGES.find((entry) => entry.code === lang) ?? LANGUAGES[0];
    return { lang, locale: language.locale, setLang, t };
  }, [lang, t]);

  return <I18n.Provider value={value}>{children}</I18n.Provider>;
}

/** `t` and the current locale. Outside a provider it still works, in English. */
export function useI18n() {
  return useContext(I18n) ?? { lang: "en", locale: "en-US", setLang() {}, t: (s) => s };
}

/** Just the translator, which is what most components need. */
export function useT() {
  return useI18n().t;
}

export function LanguagePicker({ className = "" }) {
  const { lang, setLang, t } = useI18n();
  return (
    <label className={`lang-picker ${className}`}>
      <span className="sr-only">{t("Language")}</span>
      <select
        value={lang}
        onChange={(event) => setLang(event.target.value)}
        aria-label={t("Language")}
      >
        {LANGUAGES.map((language) => (
          <option key={language.code} value={language.code}>
            {language.name}
          </option>
        ))}
      </select>
    </label>
  );
}
