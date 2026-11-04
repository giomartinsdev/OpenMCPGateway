// Light or dark, remembered per browser. Light is the default: this is an
// operations panel read in daylight, and a first visit should not be a dark
// theme nobody asked for. Dark is the preference, not the premise.
//
// The choice is applied by an inline script in index.html before first paint —
// doing it here would show the wrong palette for a frame. This module only owns
// the switch.
//
// A tiny external store rather than a context: two call sites in the tree and a
// non-React reader (`currentTheme`) both need it, and a context would thread a
// prop through every caller to answer the same question.

import { useCallback, useSyncExternalStore } from "react";

const KEY = "omg.theme";
const listeners = new Set();

function read() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

let theme = typeof document === "undefined" ? "light" : read();

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getTheme() {
  return theme;
}

export function setTheme(next) {
  theme = next === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private mode: the choice just does not outlive the tab.
  }
  listeners.forEach((listener) => listener());
}

/** `[theme, setTheme]`, shared by every component that asks. */
export function useTheme() {
  const value = useSyncExternalStore(subscribe, getTheme, getTheme);
  const update = useCallback((next) => setTheme(next), []);
  return [value, update];
}

/** The value a non-React reader needs at call time. */
export function currentTheme() {
  return theme;
}

/**
 * Both themes, side by side.
 *
 * A single toggling button hides half the system: a reader who lands in light
 * never learns there is a dark one, and the icon alone does not say which theme
 * you would get. Two labelled states cost 90px and remove the guess.
 */
export function ThemeToggle({ theme: value, onSelect, className = "" }) {
  return (
    <div className={`seg theme-seg ${className}`} role="group" aria-label="Theme">
      <button
        type="button"
        className={value === "light" ? "active" : undefined}
        aria-pressed={value === "light"}
        onClick={() => onSelect("light")}
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" />
          <path d="M8 1.2v1.3M8 13.5v1.3M1.2 8h1.3M13.5 8h1.3M3.2 3.2l.9.9M11.9 11.9l.9.9M12.8 3.2l-.9.9M4.1 11.9l-.9.9" />
        </svg>
        Light
      </button>
      <button
        type="button"
        className={value === "dark" ? "active" : undefined}
        aria-pressed={value === "dark"}
        onClick={() => onSelect("dark")}
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
          <path d="M13.4 9.2A5.7 5.7 0 0 1 6.8 2.6a5.9 5.9 0 1 0 6.6 6.6Z" />
        </svg>
        Dark
      </button>
    </div>
  );
}
