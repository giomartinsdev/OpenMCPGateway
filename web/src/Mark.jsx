// The animated mark: three dots hopping in sequence, one accent (--apur) and two
// ink. Same shape as the static `.logo-mark` bar it replaces, so the rail and
// the landing nav stay recognisably the same product.
//
// Lottie costs a 170 KB player for a three-keyframe loop, so it is treated as
// an enhancement rather than the mark itself: three CSS dots render first, the
// player arrives as its own lazy chunk, and a reduced-motion user never
// downloads it at all — they get the still dots, which is the same picture.
//
// The animation is decoration: `aria-hidden`, and never the only cue for state.

import { useEffect, useRef } from "react";
import markData from "./mark.json";

const REDUCED = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

let player;

export function Mark({ size = 34, className = "" }) {
  const host = useRef(null);

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
        animationData: markData,
      });
    });

    return () => {
      cancelled = true;
      animation?.destroy();
    };
  }, []);

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
