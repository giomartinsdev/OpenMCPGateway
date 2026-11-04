// A two-column flow: requests enter through a gateway and leave through an
// outcome.
//
// Hand-drawn SVG rather than a charting dependency: this is sixty lines of
// geometry, and the alternative is a few hundred kilobytes to draw boxes and
// curves. It is also the only chart here that a screen reader could plausibly
// read, so each link carries a `<title>` with the pair and the count.

import { useT } from "./i18n.jsx";
import { Empty, outcomeLabel } from "./ui.jsx";

// 1000 wide so the drawing renders at ~1:1 inside a full-width card: a viewBox
// that stretches makes the 11px labels come out at 14px on a laptop, and a
// 132px gutter is not enough for "401 unauthenticated · 3".
const WIDTH = 1000;
const LABEL = 200; // room for the gateway names on the left and outcome names right
const GAP = 12; // vertical space between two nodes in a column
const PAD = 6;

// A fixed order for the right column: allowed first, then refusals in the order
// an operator reads them. Sorting by volume would move the outcome you are
// looking for on every refresh.
const ORDER = ["allowed", "unauthenticated", "forbidden", "rate_limited"];

function rank(outcome) {
  const index = ORDER.indexOf(outcome);
  return index === -1 ? ORDER.length : index;
}

/** Stack the flows into nodes: `[{key, value}]` per column, in draw order. */
function nodes(flows, key) {
  const totals = new Map();
  for (const flow of flows) {
    totals.set(flow[key], (totals.get(flow[key]) ?? 0) + flow.value);
  }
  return [...totals.entries()]
    .sort((a, b) => (key === "outcome" ? rank(a[0]) - rank(b[0]) : b[1] - a[1]))
    .map(([name, value]) => ({ name, value }));
}

/**
 * Lay a column out vertically, centred in `height`.
 *
 * Both columns share one scale, which is what makes a link horizontal-ish: the
 * same request is the same number of pixels on either side of the curve.
 */
function place(list, scale, height) {
  const used = list.reduce((sum, node) => sum + node.value * scale, 0);
  let y = (height - used - GAP * (list.length - 1)) / 2;
  return list.map((node) => {
    const entry = { ...node, y, height: Math.max(node.value * scale, 2) };
    y += entry.height + GAP;
    return entry;
  });
}

export function Sankey({ flows, height = 280 }) {
  const t = useT();
  const live = (flows ?? []).filter((flow) => flow.value > 0);
  const total = live.reduce((sum, flow) => sum + flow.value, 0);
  if (!total) return null;

  const left = nodes(live, "gateway");
  const right = nodes(live, "outcome");
  // One scale for both sides, computed from the column with more nodes so the
  // gaps always fit.
  const widest = Math.max(left.length, right.length);
  const scale = Math.max((height - PAD * 2 - GAP * (widest - 1)) / total, 0.01);
  const sources = place(left, scale, height);
  const targets = place(right, scale, height);

  // Cursors into each node, so consecutive links leave and arrive stacked
  // instead of all starting at the same point.
  const from = new Map(sources.map((node) => [node.name, { ...node, cursor: node.y }]));
  const into = new Map(targets.map((node) => [node.name, { ...node, cursor: node.y }]));

  const x1 = LABEL;
  const x2 = WIDTH - LABEL;
  const bend = (x1 + x2) / 2;
  const links = live
    .slice()
    .sort((a, b) => rank(a.outcome) - rank(b.outcome) || b.value - a.value)
    .map((flow) => {
      const source = from.get(flow.gateway);
      const target = into.get(flow.outcome);
      const size = Math.max(flow.value * scale, 1);
      const y1 = source.cursor;
      const y2 = target.cursor;
      source.cursor += size;
      target.cursor += size;
      return {
        ...flow,
        size,
        path:
          `M${x1},${y1} C${bend},${y1} ${bend},${y2} ${x2},${y2} ` +
          `L${x2},${y2 + size} C${bend},${y2 + size} ${bend},${y1 + size} ` +
          `${x1},${y1 + size} Z`,
      };
    });

  return (
    <div className="sankey">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={t("Request flow")}
      >
        {links.map((link) => (
          <path
            key={`${link.gateway}-${link.outcome}`}
            d={link.path}
            className={`sankey-link ${link.outcome}`}
          >
            <title>
              {`${link.gateway} → ${outcomeLabel(t, link.outcome)} · ${link.value}`}
            </title>
          </path>
        ))}
        {sources.map((node) => (
          <g key={node.name}>
            <rect
              x={x1 - 3}
              y={node.y}
              width={6}
              height={node.height}
              rx={2}
              className="sankey-node"
            />
            <text x={x1 - 12} y={node.y + node.height / 2} textAnchor="end">
              {node.name}
            </text>
          </g>
        ))}
        {targets.map((node) => (
          <g key={node.name}>
            <rect
              x={x2 - 3}
              y={node.y}
              width={6}
              height={node.height}
              rx={2}
              className={`sankey-node ${node.name}`}
            />
            <text x={x2 + 12} y={node.y + node.height / 2}>
              {outcomeLabel(t, node.name)} · {node.value}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

/** Where the traffic went, for the home page. Empty is a normal first day. */
export function Flow({ stats }) {
  const t = useT();
  const flows = [];
  for (const [gateway, outcomes] of Object.entries(stats?.by_gateway_outcome ?? {})) {
    for (const [outcome, value] of Object.entries(outcomes)) {
      flows.push({ gateway, outcome, value });
    }
  }
  if (!flows.length) {
    return <Empty title={t("No traffic")}>{t("Nothing in this window.")}</Empty>;
  }
  return <Sankey flows={flows} />;
}
