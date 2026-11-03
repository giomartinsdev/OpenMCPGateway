import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Bars, Card, Chart, Details, Drawer, NoMatches, Notice, Skeleton,
  SAMPLE_REQUESTS, SAMPLE_STATS, Stat, outcomeKind, relative,
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

/** "up from 3" / "down from 9" / nothing when the window before was empty. */
function delta(now, before) {
  if (!before) return now > 0 ? "first traffic in this window" : null;
  const change = Math.round(((now - before) / before) * 100);
  if (change === 0) return "level with the previous window";
  return `${change > 0 ? "up" : "down"} ${Math.abs(change)}% from ${before}`;
}

function rejected(stats) {
  return (
    (stats?.by_outcome?.unauthenticated ?? 0) +
    (stats?.by_outcome?.forbidden ?? 0) +
    (stats?.by_outcome?.rate_limited ?? 0)
  );
}

export function Traffic() {
  const [minutes, setMinutes] = useState(60);
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("occurred_at", SORTS);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);

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

  const refused = rejected(stats);

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

      <div className="grid-4" style={{ marginBottom: 12 }}>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading requests">
          {(data) => (
            <Stat
              label="Requests"
              value={data.requests}
              hint={delta(data.requests, data.previous_requests)}
            />
          )}
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
          {(data) => (
            <Stat
              label="Rejected"
              value={rejected(data)}
              hint={
                data.requests
                  ? `${Math.round((rejected(data) / data.requests) * 100)}% of requests`
                  : "401 / 403 / 429"
              }
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading callers">
          {(data) => (
            <Stat
              label="Callers"
              value={Object.keys(data.by_ip ?? {}).length}
              hint={
                Object.keys(data.by_gateway ?? {}).length === 1
                  ? "1 gateway serving"
                  : `${Object.keys(data.by_gateway ?? {}).length} gateways serving`
              }
            />
          )}
        </Skeleton>
      </div>

      <Card
        title="Volume"
        aside={
          stats?.busiest
            ? `busiest minute ${new Date(stats.busiest.bucket).toLocaleTimeString()} · ${stats.busiest.count} requests`
            : null
        }
      >
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading the chart">
          {(data) => <Chart series={data.series} />}
        </Skeleton>
      </Card>

      <div className="grid-3" style={{ marginTop: 12 }}>
        <Card title="By outcome">
          <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading outcomes">
            {(data) => (
              <Bars
                total={data.requests}
                data={Object.fromEntries(
                  Object.entries(data.by_outcome ?? {}).map(([outcome, count]) => [
                    OUTCOME_LABEL[outcome] ?? outcome,
                    count,
                  ]),
                )}
              />
            )}
          </Skeleton>
        </Card>
        <Card title="By gateway">
          <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading gateways">
            {(data) => <Bars total={data.requests} data={data.by_gateway} />}
          </Skeleton>
        </Card>
        <Card title="Top callers">
          <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading callers">
            {(data) => <Bars total={data.requests} data={data.by_ip} />}
          </Skeleton>
        </Card>
      </div>

      {refused > 0 && (
        <p className="hint" style={{ marginTop: 12 }}>
          {refused} {refused === 1 ? "request was" : "requests were"} refused in this
          window. A refusal is not a failure of the gateway: it is the guard doing its
          job, and the row says which one.
        </p>
      )}

      <Card title="Recent requests" aside={requests ? `${requests.length} rows` : null}>
        <Skeleton
          data={requests}
          sample={SAMPLE_REQUESTS}
          label="Loading recent requests"
          empty={requests?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title="Nothing recorded yet"
            >
              Requests appear here as they arrive.
            </NoMatches>
          }
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
                    <tr
                      key={request.event_id}
                      className="clickable"
                      tabIndex={0}
                      onClick={() => setOpen(request)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") setOpen(request);
                      }}
                    >
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

      {open && (
        <Drawer
          title={OUTCOME_LABEL[open.outcome] ?? open.outcome}
          sub={open.event_id}
          onClose={() => setOpen(null)}
          actions={
            <button
              className="ghost sm"
              onClick={() => {
                setFilters({ gateway: open.gateway });
                setOpen(null);
              }}
            >
              Filter this gateway
            </button>
          }
        >
          <Details
            rows={[
              ["When", new Date(open.occurred_at).toLocaleString()],
              ["Seen", relative(open.occurred_at)],
              ["Gateway", open.gateway],
              ["Subject", <span className="mono">{open.subject ?? "anonymous"}</span>],
              ["Outcome", <Badge kind={outcomeKind(open.outcome)}>{open.outcome}</Badge>],
              ["Status", open.status],
              ["Latency", `${open.duration_ms} ms`],
              ["Method", <span className="mono">{open.method}</span>],
              ["Tool", <span className="mono">{open.tool || "—"}</span>],
              ["IP", <span className="mono">{open.ip || "—"}</span>],
              ["Event id", <span className="mono">{open.event_id}</span>],
              ["Gateway id", <span className="mono">{open.gateway_id}</span>],
            ]}
          />
          <p className="hint" style={{ marginBottom: 0 }}>
            {open.outcome === "allowed"
              ? "The guard admitted this call and the proxy forwarded it upstream."
              : "The guard refused this call before it reached the upstream server, so nothing was forwarded."}
          </p>
        </Drawer>
      )}
    </>
  );
}
