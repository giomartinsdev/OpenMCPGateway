import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
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

// One filter per column, under the column's own label.
const FIELDS = [
  null, // when: the window selector above already says it
  { key: "gateway", label: "Gateway" },
  { key: "subject", label: "Subject" },
  { key: "outcome", label: "Outcome" },
  { key: "status", label: "Status" },
  null, // latency is a number, and nobody filters a log by milliseconds
  { key: "tool", label: "Tool" },
  { key: "ip", label: "IP" },
  { key: "id", label: "ID" },
];

const SORTS = { occurred_at: "desc", duration_ms: "desc", status: "asc", subject: "asc" };

export function Traffic() {
  const [minutes, setMinutes] = useState(60);
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("occurred_at", SORTS);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const next = await api.stats(null, minutes);
        if (alive) setStats(next);
      } catch (err) {
        if (alive) setError(err.message);
      }
    }
    load();
    const timer = setInterval(load, 10000);
    return () => { alive = false; clearInterval(timer); };
  }, [minutes]);

  const { items: requests, next, error: listError, busy, more } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.requests({ limit, before, ...query });
      return { items: page.requests, next: page.next };
    },
    { ...filters, ...sort },
  );

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

      <Notice kind="err">{error || listError}</Notice>

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
                    <SortHeader
                      field="occurred_at"
                      label="When"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>Gateway</th>
                    <SortHeader
                      field="subject"
                      label="Subject"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>Outcome</th>
                    <SortHeader
                      field="status"
                      label="Status"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <SortHeader
                      field="duration_ms"
                      label="Latency"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <th>Tool</th>
                    <th>IP</th>
                    <th>ID</th>
                  </tr>
                  <FilterRow fields={FIELDS} value={filters} onChange={setFilters} />
                </thead>
                <tbody>
                  {rows.map((request) => (
                    <tr key={request.event_id}>
                      <td className="hint nowrap">{relative(request.occurred_at)}</td>
                      <td className="nowrap">{request.gateway}</td>
                      <td className="mono">{request.subject ?? "—"}</td>
                      <td><Badge kind={outcomeKind(request.outcome)}>{request.outcome}</Badge></td>
                      <td className="num">{request.status}</td>
                      <td className="num">{request.duration_ms} ms</td>
                      <td className="mono hint">{request.tool || "—"}</td>
                      <td className="mono hint">{request.ip || "—"}</td>
                      <td className="mono hint">{request.event_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
        <LoadMore next={next} busy={busy} onClick={more} />
      </Card>
    </>
  );
}
