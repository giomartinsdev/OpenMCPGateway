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
// A node never gets thinner than this, however small its share. With the
// gateways folded there are at most thirteen rows, so even every row at the
// floor only grows the box by thirteen pixels.
const MIN_NODE = 1;

// How many gateways the left column draws before folding the tail into one
// node. "7 days" in a deployment with thirty gateways is thirty rows of 11px
// text: at the 12px gap that is 408px of column inside a 280px box, which
// overflows on both axes. Seven named gateways is what someone actually reads;
// the rest is a number.
const MAX_NODES = 8;

// 11px JetBrains Mono advances about 0.6em, so a gutter of LABEL units fits this
// many characters. Names longer than that are truncated rather than allowed to
// write over the card edge.
const MAX_CHARS = Math.floor((LABEL - 14) / 6.6);

// A fixed order for the right column: allowed first, then refusals in the order
// an operator reads them. Sorting by volume would move the outcome you are
// looking for on every refresh.
const ORDER = ["allowed", "unauthenticated", "forbidden", "rate_limited", "error"];

function rank(outcome) {
  const index = ORDER.indexOf(outcome);
  return index === -1 ? ORDER.length : index;
}

/** Clip a label to the gutter, keeping the part that identifies it. */
function clip(text) {
  const value = String(text);
  return value.length > MAX_CHARS ? `${value.slice(0, MAX_CHARS - 1)}…` : value;
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
 * Keep the biggest gateways by volume, fold the tail into one row.
 *
 * The fold happens on the *gateway*, never on the outcome: the right column is a
 * fixed vocabulary of five names and is what the chart is for. Folding it would
 * answer a different question.
 */
function foldGateways(flows, limit, otherLabel) {
  const totals = nodes(flows, "gateway");
  if (totals.length <= limit) return flows;
  const keep = new Set(totals.slice(0, limit - 1).map((node) => node.name));
  const rest = new Map();
  const kept = [];
  for (const flow of flows) {
    if (keep.has(flow.gateway)) {
      kept.push(flow);
      continue;
    }
    rest.set(flow.outcome, (rest.get(flow.outcome) ?? 0) + flow.value);
  }
  for (const [outcome, value] of rest) {
    kept.push({ gateway: otherLabel, outcome, value });
  }
  return kept;
}

/**
 * The height a column actually occupies at a given scale.
 *
 * The box follows this rather than the other way round: sizing the box from
 * `value * scale` while drawing `max(value * scale, MIN_NODE)` is how a column
 * ends up taller than the space it was centred in, and the top row lands at a
 * negative `y`.
 */
function stackHeight(list, scale) {
  const drawn = list.reduce(
    (sum, node) => sum + Math.max(node.value * scale, MIN_NODE),
    0,
  );
  return drawn + GAP * Math.max(0, list.length - 1);
}

/**
 * Lay a column out vertically, centred in `box`.
 *
 * Both columns share one scale, which is what makes a link horizontal-ish: the
 * same request is the same number of pixels on either side of the curve.
 */
function place(list, scale, box) {
  const heights = list.map((node) => Math.max(node.value * scale, MIN_NODE));
  const used = heights.reduce((sum, value) => sum + value, 0);
  let y = Math.max(PAD, (box - used - GAP * (list.length - 1)) / 2);
  return list.map((node, index) => {
    const entry = { ...node, y, height: heights[index] };
    y += entry.height + GAP;
    return entry;
  });
}

export function Sankey({ flows, height = 280 }) {
  const t = useT();
  const moving = (flows ?? []).filter((flow) => flow.value > 0);
  const otherLabel = t("{count} others", {
    count: Math.max(0, nodes(moving, "gateway").length - (MAX_NODES - 1)),
  });
  const live = foldGateways(moving, MAX_NODES, otherLabel);
  const total = live.reduce((sum, flow) => sum + flow.value, 0);
  if (!total) return null;

  const left = nodes(live, "gateway");
  const right = nodes(live, "outcome");
  const widest = Math.max(left.length, right.length);
  // One scale for both sides, computed from the column with more nodes so the
  // gaps always fit. No floor on the scale itself: a deployment with a million
  // requests in the window gets thin rows, which is what it should get. A floor
  // there would multiply the total *up* and overflow the box.
  const scale = Math.max((height - PAD * 2 - GAP * (widest - 1)) / total, 1e-6);
  const box = Math.max(
    height,
    PAD * 2 + Math.max(stackHeight(left, scale), stackHeight(right, scale)),
  );
  const sources = place(left, scale, box);
  const targets = place(right, scale, box);

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
      // No floor here: a link thinner than a pixel is honest, and rounding it up
      // would make the links of a node add up to more than the node's own height.
      const size = flow.value * scale;
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
        viewBox={`0 0 ${WIDTH} ${box}`}
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
            {/* The visible label is clipped to the gutter; the `<title>` keeps
                the full name for the hover tooltip and the screen reader. */}
            <title>{node.name}</title>
            <rect
              x={x1 - 3}
              y={node.y}
              width={6}
              height={node.height}
              rx={2}
              className="sankey-node"
            />
            <text x={x1 - 12} y={node.y + node.height / 2} textAnchor="end">
              {clip(node.name)}
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
              {clip(`${outcomeLabel(t, node.name)} · ${node.value}`)}
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
