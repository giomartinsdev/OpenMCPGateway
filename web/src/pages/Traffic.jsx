import { useEffect, useState } from "react";
import { api } from "../api.js";
import { download, indentJson } from "../format.js";
import { useI18n, useT } from "../i18n.jsx";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Bars, Card, Chart, Details, Drawer, Empty, NoMatches, Notice, Skeleton,
  SAMPLE_REQUESTS, SAMPLE_STATS, Stat, outcomeKind, outcomeLabel, relative,
} from "../ui.jsx";

const WINDOWS = [
  [60, "1 hour"],
  [360, "6 hours"],
  [1440, "24 hours"],
  [10080, "7 days"],
];

// One filter per column, under the column's own label.
const FIELDS = [
  null, // when: the window selector above already says it
  { key: "gateway", label: "Gateway" },
  { key: "subject", label: "Subject" },
  { key: "outcome", label: "Outcome" },
  { key: "status", label: "Status" },
  null, // latency is a number, and nobody filters a log by milliseconds
  null, // size is a number for the same reason
  { key: "tool", label: "Tool" },
  { key: "ip", label: "IP" },
  { key: "id", label: "ID" },
];

const SORTS = { occurred_at: "desc", duration_ms: "desc", status: "asc", subject: "asc" };

/** "up 12% from 9" / nothing when the window before was empty. */
function delta(t, now, before) {
  if (!before) return now > 0 ? t("first traffic in this window") : null;
  const change = Math.round(((now - before) / before) * 100);
  if (change === 0) return t("level with the previous window");
  return change > 0
    ? t("up {change}% from {before}", { change, before })
    : t("down {change}% from {before}", { change: Math.abs(change), before });
}

function rejected(stats) {
  return (
    (stats?.by_outcome?.unauthenticated ?? 0) +
    (stats?.by_outcome?.forbidden ?? 0) +
    (stats?.by_outcome?.rate_limited ?? 0)
  );
}

/**
 * "1.2 kB" — a size a person can compare at a glance.
 *
 * Binary units, because this counts bytes of JSON: `1 kB` meaning 1024 is the
 * convention every tool on the other side of this log already uses, and mixing
 * it with the decimal one would make two rows disagree about the same call.
 */
function bytes(count) {
  const value = Number(count ?? 0);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "kB", "MB", "GB"];
  let scaled = value;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const rounded = scaled >= 100 || unit === 0 ? Math.round(scaled) : scaled.toFixed(1);
  return `${rounded} ${units[unit]}`;
}

/**
 * One preview, or the sentence that explains why there is none.
 *
 * `<pre>` and not a table cell: the body is JSON, and re-flowing it to a column
 * width is how a preview becomes unreadable exactly when someone needs it. It
 * scrolls horizontally instead of wrapping, so a long line stays one line.
 */
function PayloadBlock({ title, body, empty, file }) {
  const t = useT();
  const [raw, setRaw] = useState(false);
  if (!body) {
    return (
      <div className="payload-block">
        <h3 className="payload-title">{title}</h3>
        <p className="hint" style={{ margin: 0 }}>{empty}</p>
      </div>
    );
  }
  const shaped = indentJson(body);
  const formatted = shaped !== body;
  const text = raw || !formatted ? body : shaped;
  // The preview is a prefix: it ends in an ellipsis when the body was longer.
  const cut = body.endsWith("…");
  return (
    <div className="payload-block">
      <div className="payload-head">
        <h3 className="payload-title">{title}</h3>
        <div className="row">
          <span className="hint">{t("{count} characters", { count: body.length })}</span>
          {formatted ? (
            <button className="ghost sm" onClick={() => setRaw(!raw)}>
              {raw ? t("Format") : t("Raw")}
            </button>
          ) : null}
          <button className="ghost sm" onClick={() => download(file, text)}>
            {t("Download")}
          </button>
        </div>
      </div>
      <pre className="payload-body" tabIndex={0}>{text}</pre>
      {cut ? <p className="hint payload-note">{t("This is the kept prefix: the body was longer and was cut before it was stored.")}</p> : null}
    </div>
  );
}

export function Traffic() {
  const t = useT();
  const { locale } = useI18n();
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
          <h1>{t("Traffic")}</h1>
          <div className="sub">
            {t("Every request the gateway admitted or refused, across all routes.")}
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
                {t(label)}
              </button>
            ))}
          </div>
          {/* A plain link, not a fetch: the response is a file, and the browser
              saving it is the whole feature. Filters travel with it, so the
              export is the table the operator is looking at. */}
          <a className="btn ghost sm" href={api.requestsCsvUrl(filters)} download>
            {t("Export CSV")}
          </a>
        </div>
      </div>

      <Notice kind="err">{error || listError}</Notice>

      <div className="grid-4" style={{ marginBottom: 12 }}>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading requests")}>
          {(data) => (
            <Stat
              label={t("Requests")}
              value={data.requests}
              hint={delta(t, data.requests, data.previous_requests)}
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading latency")}>
          {(data) => (
            <Stat
              label={t("Avg latency")}
              value={`${data.avg_duration_ms} ms`}
              // The average hides the tail and the peak is one unlucky call.
              // p95/p99 ride in the hint: a percentile is a second reading of
              // the same number, not a fifth card on a four-card row.
              hint={
                data.p99_duration_ms
                  ? t("p95 {p95} ms · p99 {p99} ms · peak {ms} ms", {
                      p95: data.p95_duration_ms,
                      p99: data.p99_duration_ms,
                      ms: data.max_duration_ms,
                    })
                  : t("peak {ms} ms", { ms: data.max_duration_ms })
              }
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading rejections")}>
          {(data) => (
            <Stat
              label={t("Rejected")}
              value={rejected(data)}
              hint={
                data.requests
                  ? t("{percent}% of requests", {
                      percent: Math.round((rejected(data) / data.requests) * 100),
                    })
                  : "401 / 403 / 429"
              }
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading callers")}>
          {(data) => (
            <Stat
              label={t("Callers")}
              value={Object.keys(data.by_ip ?? {}).length}
              hint={
                Object.keys(data.by_gateway ?? {}).length === 1
                  ? t("1 gateway serving")
                  : t("{count} gateways serving", {
                      count: Object.keys(data.by_gateway ?? {}).length,
                    })
              }
            />
          )}
        </Skeleton>
      </div>

      <Card
        title={t("Volume")}
        aside={
          stats?.busiest
            ? t("busiest minute {time} · {count} requests", {
                time: new Date(stats.busiest.bucket).toLocaleTimeString(locale),
                count: stats.busiest.count,
              })
            : null
        }
      >
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading the chart")}>
          {(data) => <Chart series={data.series} />}
        </Skeleton>
      </Card>

      <div className="grid-4" style={{ marginTop: 12 }}>
        <Card title={t("By outcome")}>
          <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading outcomes")}>
            {(data) => (
              <Bars
                total={data.requests}
                data={Object.fromEntries(
                  Object.entries(data.by_outcome ?? {}).map(([outcome, count]) => [
                    outcomeLabel(t, outcome),
                    count,
                  ]),
                )}
              />
            )}
          </Skeleton>
        </Card>
        <Card title={t("By gateway")}>
          <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading gateways")}>
            {(data) => <Bars total={data.requests} data={data.by_gateway} />}
          </Skeleton>
        </Card>
        <Card title={t("Top callers")}>
          <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading callers")}>
            {(data) => <Bars total={data.requests} data={data.by_ip} />}
          </Skeleton>
        </Card>
        {/* Tools, not callers: the allowlist is a set of decisions, and a
            decision nobody can audit against usage is a list that only grows. */}
        <Card title={t("By tool")}>
          <Skeleton
            data={stats}
            sample={SAMPLE_STATS}
            label={t("Loading tools")}
            empty={stats && Object.keys(stats.by_tool ?? {}).length === 0}
            emptyMessage={<Empty>{t("No tools called in this window.")}</Empty>}
          >
            {(data) => <Bars total={data.requests} data={data.by_tool} />}
          </Skeleton>
        </Card>
      </div>

      {refused > 0 && (
        <p className="hint" style={{ marginTop: 12 }}>
          {refused === 1
            ? t("1 request was refused in this window. A refusal is not a failure of the gateway: it is the guard doing its job, and the row says which one.")
            : t("{count} requests were refused in this window. A refusal is not a failure of the gateway: it is the guard doing its job, and the row says which one.", { count: refused })}
        </p>
      )}

      <Card
        title={t("Recent requests")}
        aside={requests ? t("{count} rows", { count: requests.length }) : null}
      >
        <Skeleton
          data={requests}
          sample={SAMPLE_REQUESTS}
          label={t("Loading recent requests")}
          empty={requests?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title={t("Nothing recorded yet")}
            >
              {t("Requests appear here as they arrive.")}
            </NoMatches>
          }
        >
          {(rows) => (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <SortHeader
                      field="occurred_at"
                      label="When"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>{t("Gateway")}</th>
                    <SortHeader
                      field="subject"
                      label="Subject"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>{t("Outcome")}</th>
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
                    <th className="num">{t("Size")}</th>
                    <th>{t("Tool")}</th>
                    <th>{t("IP")}</th>
                    <th>{t("ID")}</th>
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
                      <td>
                        <Badge kind={outcomeKind(request.outcome)}>
                          {outcomeLabel(t, request.outcome)}
                        </Badge>
                      </td>
                      <td className="num">{request.status}</td>
                      <td className="num">{request.duration_ms} ms</td>
                      <td className="num mono hint">
                        {bytes(request.bytes_in)} → {bytes(request.bytes_out)}
                      </td>
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
        <LoadMore next={next} busy={busy} onClick={more} shown={requests?.length} />
      </Card>

      {open && (
        <Drawer
          title={outcomeLabel(t, open.outcome)}
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
              {t("Filter this gateway")}
            </button>
          }
        >
          <Details
            rows={[
              [t("When"), new Date(open.occurred_at).toLocaleString(locale)],
              [t("Seen"), relative(open.occurred_at)],
              [t("Gateway"), open.gateway],
              [t("Subject"), <span className="mono">{open.subject ?? t("anonymous")}</span>],
              [
                t("Outcome"),
                <Badge kind={outcomeKind(open.outcome)}>
                  {outcomeLabel(t, open.outcome)}
                </Badge>,
              ],
              [t("Status"), open.status],
              [t("Latency"), `${open.duration_ms} ms`],
              // The pair that turns a row into a trace. Shown even when zero,
              // because "0 B out" on an allowed call is itself the finding: the
              // upstream answered with an empty body.
              [
                t("Payload"),
                <span className="inline-pair">
                  <span className="mono">{bytes(open.bytes_in)}</span>
                  <span className="hint">{t("in")}</span>
                  <span className="arrow" aria-hidden="true">→</span>
                  <span className="mono">{bytes(open.bytes_out)}</span>
                  <span className="hint">{t("out")}</span>
                </span>,
              ],
              [t("Method"), <span className="mono">{open.method}</span>],
              [t("Tool"), <span className="mono">{open.tool || "—"}</span>],
              [t("IP"), <span className="mono">{open.ip || "—"}</span>],
              // Only when there is something to say: a row that is always present
              // and usually empty trains the reader to skip the section.
              // Same sentence the guard stored; translated on the way out.
              ...(open.reason ? [[t("Reason"), t(open.reason)]] : []),
              ...(open.request_id
                ? [[t("Request id"), <span className="mono">{open.request_id}</span>]]
                : []),
              [t("Event id"), <span className="mono">{open.event_id}</span>],
              [t("Gateway id"), <span className="mono">{open.gateway_id}</span>],
            ]}
          />
          {/* The bodies, as a direction rather than a table: "what I sent" and
              "what came back" are one question asked twice, and a reader
              comparing two previews reads them against each other. Each block is
              absent when there is nothing to show, so an empty box never stands
              in for an unread body. */}
          <PayloadBlock
            title={t("What went in")}
            body={open.request_preview}
            empty={t("The request body was not kept for this call.")}
            file={`${open.event_id}-request.json`}
          />
          <PayloadBlock
            title={t("What came back")}
            body={open.response_preview}
            empty={t("The upstream sent nothing, or the call was refused before it was forwarded.")}
            file={`${open.event_id}-response.json`}
          />
          <p className="hint" style={{ marginBottom: 0 }}>
            {open.outcome === "allowed"
              ? t("The guard admitted this call and the proxy forwarded it upstream.")
              : t("The guard refused this call before it reached the upstream server, so nothing was forwarded.")}
          </p>
        </Drawer>
      )}
    </>
  );
}
