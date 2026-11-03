import { useEffect, useState } from "react";
import { api } from "../api.js";
import {
  Badge, Card, Chart, Empty, Notice, Skeleton, SAMPLE_REQUESTS,
  SAMPLE_STATS, Stat, outcomeKind, relative,
} from "../ui.jsx";

const WINDOWS = [
  [60, "1 hour"],
  [360, "6 hours"],
  [1440, "24 hours"],
  [10080, "7 days"],
];

const OUTCOME_LABEL = {
  allowed: "allowed",
  unauthenticated: "401 unauthenticated",
  forbidden: "403 forbidden",
  rate_limited: "429 rate limited",
};

export function Traffic() {
  const [minutes, setMinutes] = useState(60);
  const [stats, setStats] = useState(null);
  const [requests, setRequests] = useState(null);
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
      <div className="page-head">
        <div>
          <h1>Traffic</h1>
          <div className="sub">
            Every request the gateway admitted or refused, across all routes.
          </div>
        </div>
        <div className="actions">
          <div className="seg">
            {WINDOWS.map(([value, label]) => (
              <button
                key={value}
                className={minutes === value ? "active" : ""}
                onClick={() => setMinutes(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      <div className="grid-3" style={{ marginBottom: 12 }}>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading requests">
          {(data) => <Stat label="Requests" value={data.requests} />}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading latency">
          {(data) => (
            <Stat
              label="Avg latency"
              value={`${data.avg_duration_ms} ms`}
              hint={`peak ${data.max_duration_ms} ms`}
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading rejections">
          {() => <Stat label="Rejected" value={rejected} hint="401 / 403 / 429" />}
        </Skeleton>
      </div>

      <Card title="Volume">
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading the chart">
          {(data) => <Chart series={data.series} />}
        </Skeleton>
      </Card>

      <div className="grid-2" style={{ marginTop: 12 }}>
        <Card title="By outcome">
          <Skeleton
            data={stats}
            sample={SAMPLE_STATS}
            label="Loading outcomes"
            empty={stats && Object.keys(stats.by_outcome ?? {}).length === 0}
            emptyMessage={<Empty title="No traffic">Nothing in this window.</Empty>}
          >
            {(data) => (
              <table>
                <tbody>
                  {Object.entries(data.by_outcome ?? {}).map(([outcome, count]) => (
                    <tr key={outcome}>
                      <td><Badge kind={outcomeKind(outcome)}>{OUTCOME_LABEL[outcome] ?? outcome}</Badge></td>
                      <td className="num">{count}</td>
                      <td className="num hint">
                        {data.requests ? Math.round((count / data.requests) * 100) : 0}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Skeleton>
        </Card>
        <Card title="Top callers">
          <Skeleton
            data={stats}
            sample={SAMPLE_STATS}
            label="Loading callers"
            empty={stats && Object.keys(stats.by_ip ?? {}).length === 0}
            emptyMessage={<Empty title="No callers">Nothing in this window.</Empty>}
          >
            {(data) => (
              <table>
                <tbody>
                  {Object.entries(data.by_ip ?? {}).map(([ip, count]) => (
                    <tr key={ip}>
                      <td className="mono">{ip || "unknown"}</td>
                      <td className="num">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Skeleton>
        </Card>
      </div>

      <Card title="Recent requests" aside={requests ? `${requests.length} rows` : null}>
        <Skeleton
          data={requests}
          sample={SAMPLE_REQUESTS}
          label="Loading recent requests"
          empty={requests?.length === 0}
          emptyMessage={<Empty title="Nothing recorded yet">Requests appear here as they arrive.</Empty>}
        >
          {(rows) => (
            <div className="table-scroll">
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
                  {rows.map((request, index) => (
                    <tr key={`${request.occurred_at}-${index}`}>
                      <td className="hint nowrap">{relative(request.occurred_at)}</td>
                      <td className="nowrap">{names[request.gateway_id] ?? request.gateway_id}</td>
                      <td className="mono">{request.subject ?? "—"}</td>
                      <td><Badge kind={outcomeKind(request.outcome)}>{request.outcome}</Badge></td>
                      <td className="num">{request.status}</td>
                      <td className="num">{request.duration_ms} ms</td>
                      <td className="mono hint">{request.ip || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
      </Card>
    </>
  );
}
