// The animated mark: three dots hopping in sequence, one accent (--apur) and two
// ink. The same three dots are the favicon and the landing nav, so the tab, the
// pitch and the panel are recognisably one product.
//
// Lottie costs a 170 KB player for a three-keyframe loop, so it is treated as
// an enhancement rather than the mark itself: three CSS dots render first, the
// player arrives as its own lazy chunk, and a reduced-motion user never
// downloads it at all — they get the still dots, which is the same picture.
//
// The two palette colours in `mark.json` are placeholders: they are rewritten
// from the live CSS variables before the player loads, so the mark follows the
// theme like every other element instead of pinning the dark palette.
//
// The animation is decoration: `aria-hidden`, and never the only cue for state.

import { useEffect, useRef } from "react";
import markData from "./mark.json";
import { useTheme } from "./theme.jsx";

const REDUCED = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

let player;

/** "#3dd68c" → [0.239, 0.839, 0.549, 1], the shape Lottie's fills want. */
function rgb(css, fallback) {
  const hex = /^#([0-9a-f]{6})$/i.exec(css.trim());
  if (!hex) return fallback;
  const value = parseInt(hex[1], 16);
  return [
    ((value >> 16) & 255) / 255,
    ((value >> 8) & 255) / 255,
    (value & 255) / 255,
    1,
  ];
}

/** The same animation with the two fills taken from the current palette. */
function themed(data) {
  const styles = getComputedStyle(document.documentElement);
  const accent = rgb(styles.getPropertyValue("--apur"), [0.239, 0.839, 0.549, 1]);
  const ink = rgb(styles.getPropertyValue("--fg"), [0.98, 0.98, 0.976, 1]);
  const copy = structuredClone(data);
  for (const layer of copy.layers ?? []) {
    for (const group of layer.shapes ?? []) {
      for (const item of group.it ?? []) {
        if (item.ty !== "fl") continue;
        // dot-a and dot-c are the accent; dot-b is ink.
        item.c = { a: 0, k: layer.nm === "dot-b" ? ink : accent };
      }
    }
  }
  return copy;
}

export function Mark({ size = 34, className = "" }) {
  const host = useRef(null);
  const [theme] = useTheme();

  useEffect(() => {
    // Motion is the only thing the player adds, so a reader who asked for less
    // motion never gets the player. Saves them 48 KB and an animation.
    if (REDUCED()) return;

    let animation;
    let cancelled = false;

    import("lottie-web/build/player/lottie_light").then((mod) => {
      player ??= mod.default ?? mod;
      if (cancelled || !host.current) return;
      // lottie appends its own <svg>; the fallback dots have to go first or the
      // animation renders on top of them.
      host.current.replaceChildren();
      animation = player.loadAnimation({
        container: host.current,
        renderer: "svg",
        loop: true,
        autoplay: true,
        animationData: themed(markData),
      });
    });

    return () => {
      cancelled = true;
      animation?.destroy();
    };
  }, [theme]);

  return (
    <div
      ref={host}
      className={`mark ${className}`}
      style={{ width: size, height: size * 0.375 }}
      aria-hidden="true"
    >
      <span className="mark-dot" />
      <span className="mark-dot dim" />
      <span className="mark-dot" />
    </div>
  );
}
