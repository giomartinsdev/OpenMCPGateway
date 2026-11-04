// The mark: an O that is a padlock, with a shield in the counter. One path set,
// one colour — the accent — so the tab, the landing nav and the panel are
// recognisably one product.
//
// It used to be three Lottie dots. The loop cost a 170 KB player and a lazy
// chunk to say "we are alive", and a static mark says the same thing in 900
// bytes with no script at all. The animation is gone, not deferred.
//
// `aria-hidden`: the product name is always next to it, and a logo is not a
// second way to read it.

export function Mark({ size = 28, className = "" }) {
  return (
    <svg
      className={`mark ${className}`}
      viewBox="0 0 360 420"
      width={Math.round(size * (360 / 420))}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      {/* shackle */}
      <path d="M 125 130 A 55 55 0 0 1 235 130 L 235 155 L 205 155 L 205 130 A 25 25 0 0 0 155 130 L 155 155 L 125 155 Z" />
      {/* shield in the counter */}
      <path d="M 152 220 L 208 220 L 208 245 C 208 268 180 282 180 282 C 180 282 152 268 152 245 Z" />
      {/* the O */}
      <path
        fillRule="evenodd"
        d="M 180 130 A 115 115 0 1 0 180 360 A 115 115 0 1 0 180 130 Z M 180 188 A 57 57 0 1 1 180 302 A 57 57 0 1 1 180 188 Z"
      />
    </svg>
  );
}
