// Primitives only. No component library: the whole UI is tables, cards and
// badges, and a dependency would cost more than these 80 lines.

export function Badge({ kind = "mute", children }) {
  return (
    <span className={`badge ${kind}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

export function Metric({ label, value, hint }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <h2>{title}</h2>
          {actions && <div className="row">{actions}</div>}
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
        className="ghost copy"
        onClick={() => navigator.clipboard?.writeText(value)}
        title="Copy"
      >
        copy
      </button>
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function Chart({ series }) {
  if (!series?.length) return <Empty>No traffic in this window.</Empty>;
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

export function outcomeKind(outcome) {
  if (outcome === "allowed") return "ok";
  if (outcome === "rate_limited") return "warn";
  if (outcome === "forbidden") return "err";
  if (outcome === "unauthenticated") return "err";
  return "mute";
}

export function relative(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}
