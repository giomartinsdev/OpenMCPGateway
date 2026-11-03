// Light or dark, remembered per browser.
//
// The choice is applied by an inline script in index.html before first paint —
// doing it here would show the wrong palette for a frame. This module only owns
// the switch.
//
// A tiny external store rather than a context: the mark's animation needs to
// repaint itself when the palette flips, and a prop threaded through every
// caller would be a second, worse source of truth.

import { useCallback, useSyncExternalStore } from "react";

const KEY = "omg.theme";
const listeners = new Set();

function read() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

let theme = typeof document === "undefined" ? "dark" : read();

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getTheme() {
  return theme;
}

function setTheme(next) {
  theme = next;
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // Private mode: the choice just does not outlive the tab.
  }
  listeners.forEach((listener) => listener());
}

/** `[theme, toggle]`, shared by every component that asks. */
export function useTheme() {
  const value = useSyncExternalStore(subscribe, getTheme, getTheme);
  const toggle = useCallback(() => setTheme(value === "dark" ? "light" : "dark"), [value]);
  return [value, toggle];
}

/** The value a non-React reader (the Lottie loader) needs at call time. */
export function currentTheme() {
  return theme;
}

/** Sun or moon, in the same 16px grid as the nav icons. */
export function ThemeToggle({ theme: value, onToggle, className = "" }) {
  const dark = value === "dark";
  return (
    <button
      type="button"
      className={`ghost icon ${className}`}
      onClick={onToggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light" : "Dark"}
    >
      <svg
        width="15"
        height="15"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        aria-hidden="true"
      >
        {dark ? (
          <>
            <circle cx="8" cy="8" r="3.1" />
            <path d="M8 1.4v1.5M8 13.1v1.5M1.4 8h1.5M13.1 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" />
          </>
        ) : (
          <path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z" />
        )}
      </svg>
    </button>
  );
}
