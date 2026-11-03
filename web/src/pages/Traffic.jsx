import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Chart, Empty, Metric, Notice, outcomeKind, relative } from "../ui.jsx";

const WINDOWS = [
  [60, "1 hour"],
  [360, "6 hours"],
  [1440, "24 hours"],
  [10080, "7 days"],
];

export function Traffic() {
  const [minutes, setMinutes] = useState(60);
  const [stats, setStats] = useState(null);
  const [requests, setRequests] = useState([]);
  const [gateways, setGateways] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [stats, log, list] = await Promise.all([
          api.stats(null, minutes),
          api.requests(null, 100),
          api.gateways(),
        ]);
        if (!alive) return;
        setStats(stats);
        setRequests(log.requests);
        setGateways(list.gateways);
      } catch (err) {
        if (alive) setError(err.message);
      }
    }
    load();
    const timer = setInterval(load, 10000);
    return () => { alive = false; clearInterval(timer); };
  }, [minutes]);

  const names = Object.fromEntries(gateways.map((g) => [g.id, g.name]));
  const rejected =
    (stats?.by_outcome?.unauthenticated ?? 0) +
    (stats?.by_outcome?.forbidden ?? 0) +
    (stats?.by_outcome?.rate_limited ?? 0);

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Traffic</h1>
          <div className="sub">Every request the gateway admitted or refused, across all routes.</div>
        </div>
        <div className="row">
          {WINDOWS.map(([value, label]) => (
            <button
              key={value}
              className={minutes === value ? "primary sm" : "sm"}
              onClick={() => setMinutes(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      <div className="grid-3" style={{ marginBottom: 14 }}>
        <Metric label="Requests" value={stats?.requests ?? 0} />
        <Metric label="Avg latency" value={`${stats?.avg_duration_ms ?? 0} ms`}
                hint={`peak ${stats?.max_duration_ms ?? 0} ms`} />
        <Metric label="Rejected" value={rejected} hint="401 / 403 / 429" />
      </div>

      <Card title="Volume">
        <Chart series={stats?.series} />
      </Card>

      <div className="grid-2">
        <Card title="By outcome">
          {Object.entries(stats?.by_outcome ?? {}).length === 0 ? (
            <Empty>No traffic in this window.</Empty>
          ) : (
            <table>
              <tbody>
                {Object.entries(stats.by_outcome).map(([outcome, count]) => (
                  <tr key={outcome}>
                    <td><Badge kind={outcomeKind(outcome)}>{outcome}</Badge></td>
                    <td className="num">{count}</td>
                    <td className="num hint">
                      {Math.round((count / stats.requests) * 100)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Top callers">
          {Object.entries(stats?.by_ip ?? {}).length === 0 ? (
            <Empty>No callers in this window.</Empty>
          ) : (
            <table>
              <tbody>
                {Object.entries(stats.by_ip).map(([ip, count]) => (
                  <tr key={ip}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{ip}</td>
                    <td className="num">{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Recent requests">
        {requests.length === 0 ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Gateway</th>
                <th>Subject</th>
                <th>Outcome</th>
                <th className="num">Status</th>
                <th className="num">Latency</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((request, index) => (
                <tr key={`${request.occurred_at}-${index}`}>
                  <td className="hint">{relative(request.occurred_at)}</td>
                  <td>{names[request.gateway_id] ?? request.gateway_id}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{request.subject ?? "—"}</td>
                  <td><Badge kind={outcomeKind(request.outcome)}>{request.outcome}</Badge></td>
                  <td className="num">{request.status}</td>
                  <td className="num">{request.duration_ms} ms</td>
                  <td className="mono" style={{ fontSize: 11.5, color: "var(--fg-4)" }}>
                    {request.ip || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
