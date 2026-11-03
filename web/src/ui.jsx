// Primitives only. No component library beyond skullet, which is one hook and a
// wrapper: the whole UI is tables, cards and chips, and a design system would
// cost more than it saves.

import { useEffect } from "react";
import { Skeleton as SkulletSkeleton, SkeletonBox } from "skullet/react";
import { getLocale, useT } from "./i18n.jsx";

/** Whether the signed-in operator may do something. Server still decides. */
export function can(me, permission) {
  return me?.permissions?.includes(permission) === true;
}

/**
 * A measured skeleton in place of "Loading…".
 *
 * `sample` is what the real content looks like with placeholder values, so the
 * bones take the space the content will take and nothing jumps when it lands.
 * Every caller passes one: a generic grey block is worse than a spinner because
 * it lies about the layout.
 */
export function Skeleton({ data, sample, children, label, ...rest }) {
  const t = useT();
  return (
    <SkulletSkeleton data={data} sample={sample} label={label ?? t("Loading")} {...rest}>
      {children}
    </SkulletSkeleton>
  );
}

/** One bone. For the shapes a sample cannot express (a chart, a sparkline). */
export function Bone({ height = 14, width = "100%", radius = 6, style }) {
  return <SkeletonBox height={height} width={width} radius={radius} style={style} />;
}

export function Badge({ kind = "mute", mono = false, children }) {
  return (
    <span className={`badge ${kind}${mono ? " mono" : ""}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

export function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}

/** One badge for "what does this URL accept", shared by the list and the detail
 * card. It lives here because the two drifted: the detail said "SSO · google
 * + keys" while the list said only "SSO · google", and an operator reading the
 * list concluded the gateway was login-only. */
export function AuthBadge({ gateway, t }) {
  const label = gateway.auth_mode
    ? t({ none: "open", api_key: "API key", oauth: "SSO" }[gateway.auth_mode] ?? gateway.auth_mode)
    : gateway.auth_mode;
  const extra = [
    gateway.idp,
    gateway.allow_api_key ? t("+ keys") : null,
    gateway.verifiers?.length
      ? gateway.verifiers.length === 1
        ? t("+ 1 issuer")
        : t("+ {count} issuers", { count: gateway.verifiers.length })
      : null,
  ].filter(Boolean);
  return (
    <Badge kind={gateway.auth_mode === "none" ? "warn" : "info"}>
      {[label, ...extra].join(" · ")}
    </Badge>
  );
}

export function Card({ title, aside, actions, children, className = "", flush = false }) {
  return (
    <section className={`card${flush ? " flush" : ""} ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <h2>{title}</h2>
          {actions ? <div className="row">{actions}</div> : null}
          {!actions && aside ? <div className="hint">{aside}</div> : null}
        </div>
      )}
      {children}
    </section>
  );
}

export function Notice({ kind = "info", children }) {
  if (!children) return null;
  return <div className={`notice ${kind}`}>{children}</div>;
}

/** Copy-to-clipboard line. The endpoint is the thing people paste into a client. */
export function Endpoint({ value }) {
  const t = useT();
  return (
    <div className="endpoint">
      <span>{value}</span>
      <button
        className="ghost sm copy"
        onClick={() => navigator.clipboard?.writeText(value)}
        title={t("Copy")}
      >
        {t("copy")}
      </button>
    </div>
  );
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      {title ? <strong>{title}</strong> : null}
      {children}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

/**
 * A panel that slides in from the right, over the page.
 *
 * Rows are summaries: a request row can say "429 at 12ms" and still leave the
 * question the row was opened to answer. Opening a drawer keeps the list behind
 * it — the row is still there when it closes — which a detail page does not.
 *
 * Escape and the scrim both close it: a drawer with no keyboard exit is a trap
 * for anyone not using a mouse.
 */
export function Drawer({ title, sub, onClose, actions, children }) {
  const t = useT();
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-wrap">
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {sub ? <div className="hint mono">{sub}</div> : null}
          </div>
          <div className="row">
            {actions}
            <button className="ghost sm" onClick={onClose} aria-label={t("Close")}>
              {t("Close")}
            </button>
          </div>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

/** Label/value rows. Everything a table cell was too narrow to say. */
export function Details({ rows }) {
  return (
    <dl className="details">
      {rows.filter(Boolean).map(([label, value]) => (
        <div key={label} className="detail">
          <dt>{label}</dt>
          <dd>{value === null || value === undefined || value === "" ? "—" : value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The empty state of a list that may be empty for two different reasons.
 *
 * "No events yet" is a lie when the list is empty because a filter is wrong, and
 * the reader is left looking for a box to clear in a header row that no longer
 * has any rows under it. When filters are on, say so and hand back a button.
 */
export function NoMatches({ filters, onClear, title, children, action }) {
  const t = useT();
  const active = Object.values(filters ?? {}).filter((v) => v !== "" && v != null);
  if (active.length > 0) {
    return (
      <Empty
        title={t("No rows match this filter")}
        action={
          <button className="sm" onClick={onClear}>
            {active.length === 1 ? t("Clear the filter") : t("Clear all filters")}
          </button>
        }
      >
        {t("{values} matched nothing.", {
          values: active.map((value) => `“${value}”`).join(", "),
        })}
      </Empty>
    );
  }
  return (
    <Empty title={title} action={action}>
      {children}
    </Empty>
  );
}

/** A ranked breakdown as horizontal bars.
 *
 * Wider than a table for the same information: the comparison between two rows
 * is the reason the block exists, and a column of numbers makes the reader do
 * the division in their head.
 */
export function Bars({ data, total }) {
  const t = useT();
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) {
    return <Empty title={t("No traffic")}>{t("Nothing in this window.")}</Empty>;
  }
  const peak = Math.max(...entries.map(([, count]) => count), 1);
  const sum = total ?? entries.reduce((carry, [, count]) => carry + count, 0);
  return (
    <ul className="bars">
      {entries.map(([label, count]) => (
        <li key={label}>
          <div className="bar-label">
            <span className="mono">{label || t("unknown")}</span>
            <span className="hint num">
              {count}
              {sum ? ` · ${Math.round((count / sum) * 100)}%` : ""}
            </span>
          </div>
          <div className="bar-track">
            <div style={{ width: `${(count / peak) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Chart({ series }) {
  const t = useT();
  if (!series?.length) return <Empty title={t("No traffic")}>{t("Nothing in this window.")}</Empty>;
  const peak = Math.max(...series.map((p) => p.count), 1);
  return (
    <div className="chart">
      {series.map((point) => (
        <div
          key={point.bucket}
          style={{ height: `${Math.max((point.count / peak) * 100, 3)}%` }}
          title={t("{bucket} · {count} requests", {
            bucket: point.bucket,
            count: point.count,
          })}
        />
      ))}
    </div>
  );
}

/* Placeholder shapes, used as `sample` so a skeleton has the right height
   before any data exists. Kept next to the primitives they imitate. */

export const SAMPLE_STATS = {
  requests: 0,
  previous_requests: 0,
  avg_duration_ms: 0,
  max_duration_ms: 0,
  by_ip: {},
  by_outcome: {},
  by_tool: {},
  by_gateway: {},
  by_gateway_outcome: {},
  series: [],
  busiest: null,
};

export const SAMPLE_GATEWAYS = [
  { id: "a", name: "Gateway name", slug: "slug", endpoint: "https://…/g/slug/mcp", auth_mode: "oauth", idp: "google", requests_per_minute: 120, enabled: true, updated_at: new Date().toISOString() },
  { id: "b", name: "Gateway name", slug: "slug", endpoint: "https://…/g/slug/mcp", auth_mode: "api_key", requests_per_minute: 0, enabled: true, updated_at: new Date().toISOString() },
  { id: "c", name: "Gateway name", slug: "slug", endpoint: "https://…/g/slug/mcp", auth_mode: "none", requests_per_minute: 60, enabled: false, updated_at: new Date().toISOString() },
];

export const SAMPLE_ROLES = [
  { id: "role_owner", name: "owner", permissions: ["gateways:read", "gateways:write"], builtin: true, holders: 1 },
  { id: "role_viewer", name: "viewer", permissions: ["gateways:read"], builtin: true, holders: 2 },
  { id: "role_x", name: "Role name", permissions: ["traffic:read"], builtin: false, holders: 0 },
];

export const SAMPLE_MEMBERS = [
  { operator_id: "a", email: "someone@example.com", roles: ["viewer"], role_ids: [], created_at: new Date().toISOString() },
  { operator_id: "b", email: "someone@example.com", roles: ["viewer"], role_ids: [], created_at: new Date().toISOString() },
];

export const SAMPLE_REQUESTS = [
  { occurred_at: new Date().toISOString(), subject: "api-key:key_0000", outcome: "allowed", status: 200, duration_ms: 12, ip: "10.0.0.1" },
  { occurred_at: new Date().toISOString(), subject: "google:someone@example.com", outcome: "forbidden", status: 403, duration_ms: 3, ip: "10.0.0.2" },
  { occurred_at: new Date().toISOString(), subject: null, outcome: "unauthenticated", status: 401, duration_ms: 1, ip: "10.0.0.3" },
];

export const SAMPLE_EVENTS = [
  { global_seq: 1, event_type: "gateway.created", aggregate_id: "gw_0000", actor: "someone@example.com", occurred_at: new Date().toISOString() },
  { global_seq: 2, event_type: "api_key.issued", aggregate_id: "gw_0000", actor: "someone@example.com", occurred_at: new Date().toISOString() },
  { global_seq: 3, event_type: "role.defined", aggregate_id: "role_0000", actor: "someone@example.com", occurred_at: new Date().toISOString() },
];

/** "3m ago", in the reader's language. `Intl` knows the plural rules. */
export function relative(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const format = new Intl.RelativeTimeFormat(getLocale(), { numeric: "auto", style: "narrow" });
  const seconds = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return format.format(seconds, "second");
  if (abs < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86400), "day");
}

export function outcomeKind(outcome) {
  if (outcome === "allowed") return "ok";
  if (outcome === "rate_limited") return "warn";
  if (outcome === "forbidden") return "err";
  if (outcome === "unauthenticated") return "err";
  return "mute";
}

/** The four outcomes, named for a reader. Keyed by the API's own vocabulary. */
export const OUTCOME_LABEL = {
  allowed: "allowed",
  unauthenticated: "401 unauthenticated",
  forbidden: "403 forbidden",
  rate_limited: "429 rate limited",
};

/** One place to translate an outcome, so the three tables cannot disagree. */
export function outcomeLabel(t, outcome) {
  return t(OUTCOME_LABEL[outcome] ?? outcome);
}

