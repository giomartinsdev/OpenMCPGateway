// Primitives only. No component library beyond skullet, which is one hook and a
// wrapper: the whole UI is tables, cards and chips, and a design system would
// cost more than it saves.

import { Skeleton as SkulletSkeleton, SkeletonBox } from "skullet/react";

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
export function Skeleton({ data, sample, children, label = "Loading", ...rest }) {
  return (
    <SkulletSkeleton data={data} sample={sample} label={label} {...rest}>
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
  return (
    <div className="endpoint">
      <span>{value}</span>
      <button
        className="ghost sm copy"
        onClick={() => navigator.clipboard?.writeText(value)}
        title="Copy"
      >
        copy
      </button>
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      {title ? <strong>{title}</strong> : null}
      {children}
    </div>
  );
}

export function Chart({ series }) {
  if (!series?.length) return <Empty title="No traffic">Nothing in this window.</Empty>;
  const peak = Math.max(...series.map((p) => p.count), 1);
  return (
    <div className="chart">
      {series.map((point) => (
        <div
          key={point.bucket}
          style={{ height: `${Math.max((point.count / peak) * 100, 3)}%` }}
          title={`${point.bucket} · ${point.count} requests`}
        />
      ))}
    </div>
  );
}

/* Placeholder shapes, used as `sample` so a skeleton has the right height
   before any data exists. Kept next to the primitives they imitate. */

export const SAMPLE_STATS = { requests: 0, avg_duration_ms: 0, max_duration_ms: 0, by_ip: {}, by_outcome: {}, series: [] };

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

export function relative(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function outcomeKind(outcome) {
  if (outcome === "allowed") return "ok";
  if (outcome === "rate_limited") return "warn";
  if (outcome === "forbidden") return "err";
  if (outcome === "unauthenticated") return "err";
  return "mute";
}
