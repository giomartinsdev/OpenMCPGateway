// A closed set of 16px line icons, drawn on the same grid and stroked with
// `currentColor` so an icon inherits the state of the row it sits in.
//
// Inline paths instead of an icon package: nine glyphs are ~1 KB of markup, and
// a dependency would be a build step, a version to bump and a tree-shaking
// config to trust for the same nine.

const PATHS = {
  // A route: the gateway in front of a server.
  gateway: [
    "M2.5 6.5h3l2 5 3-5h3",
    "M14.5 3.5v3h-3",
    "M2.5 12.5h11",
  ],
  pulse: ["M2 8h2.5l1.5-3.5 2 7 1.5-3.5H14"],
  logs: ["M3 4h10", "M3 8h7", "M3 12h9"],
  // Sliders, not a gear: a gear at 16px is mush, and this page is knobs.
  settings: [
    "M2 4.5h4.5",
    "M9.5 4.5h4.5",
    "M2 11.5h2.5",
    "M7.5 11.5h6.5",
    "M8.2 4.5a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0Z",
    "M7.5 11.5a1.7 1.7 0 1 0-3.4 0 1.7 1.7 0 0 0 3.4 0Z",
  ],
  // Shield with a check: roles and permissions.
  shield: ["M8 1.9 13 3.9v4.2c0 3-2.1 5.3-5 6-2.9-.7-5-3-5-6V3.9l5-2Z", "M6 8.1 7.4 9.5 10.2 6.7"],
  code: ["M5.5 5 2.5 8l3 3", "M10.5 5l3 3-3 3", "M9 3 7 13"],
  book: ["M3 2.5h5.5a1.5 1.5 0 0 1 1.5 1.5v9a2 2 0 0 0-2-2H3v-8.5Z", "M13 2.5H9.5A1.5 1.5 0 0 0 8 4v9a2 2 0 0 1 2-2h3v-8.5Z"],
  users: ["M6 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M1.5 13.5c0-2.2 2-4 4.5-4s4.5 1.8 4.5 4", "M10.5 3.2a2.5 2.5 0 0 1 0 4.8", "M11.5 9.8c1.8.5 3 2 3 3.7"],
  key: ["M10.2 2.4a3.4 3.4 0 1 0-2.9 5.1L4 10.8v2.4h2.4v-1.7h1.7V9.8h1.7l.9-.9a3.4 3.4 0 0 0-.5-6.5Z", "M11 5.4h.01"],
  external: ["M6.5 3.5h-3v9h9v-3", "M9.5 3.5h3v3", "M12.5 3.5 7.5 8.5"],
};

/**
 * One icon. `size` is the box; the stroke scales with it so a 20px icon in a
 * page heading is not a hairline.
 */
export function Icon({ name, size = 16, className = "", ...rest }) {
  const paths = PATHS[name];
  if (!paths) return null;
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

export const ICON_NAMES = Object.keys(PATHS);
