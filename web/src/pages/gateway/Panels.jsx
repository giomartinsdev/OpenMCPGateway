// The gateway detail page's pieces. Kept out of the page file so each one is a
// single concern: auth, the traffic tables, and the copy-paste guide.

import { useState } from "react";
import { api } from "../../api.js";
import { useI18n, useT } from "../../i18n.jsx";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../../paged.jsx";
import {
  Badge, Card, Chart, Details, Drawer, Empty, NoMatches, Skeleton,
  SAMPLE_REQUESTS, SAMPLE_STATS, outcomeKind, outcomeLabel, relative,
} from "../../ui.jsx";

// One filter per column, under the column's own label. The gateway is fixed by
// the page, so it is not a column and not a filter here.
const FIELDS = [
  null, // when: the table is newest-first until you sort it
  { key: "subject", label: "Subject" },
  { key: "outcome", label: "Outcome" },
  { key: "status", label: "Status" },
  null, // latency is a number, and nobody filters a log by milliseconds
  { key: "tool", label: "Tool" },
  { key: "ip", label: "IP" },
];

const SORTS = { occurred_at: "desc", duration_ms: "desc", status: "asc", subject: "asc" };

export function AuthForm({ gateway, onSaved }) {
  const t = useT();
  const [form, setForm] = useState({
    auth_mode: gateway.auth_mode,
    idp: gateway.idp ?? "",
    client_id: gateway.client_id ?? "",
    client_secret: "",
    config_url: gateway.config_url ?? "",
    required_scopes: (gateway.required_scopes ?? []).join(" "),
    allow_api_key: Boolean(gateway.allow_api_key),
    verifiers: (gateway.verifiers ?? []).map((v) => ({
      label: v.label ?? "", issuer: v.issuer ?? "",
      audience: v.audience ?? "", jwks_uri: v.jwks_uri ?? "",
    })),
    upstream_token: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  // One repeated row per extra issuer, edited in place. A row is only added on
  // demand: most gateways have none, and an empty table of inputs reads as a
  // requirement rather than an option.
  const setVerifier = (index, key) => (event) => setForm({
    ...form,
    verifiers: form.verifiers.map((v, i) => (i === index ? { ...v, [key]: event.target.value } : v)),
  });
  const addVerifier = () => setForm({
    ...form,
    verifiers: [...form.verifiers, { label: "", issuer: "", audience: "", jwks_uri: "" }],
  });
  const removeVerifier = (index) => setForm({
    ...form,
    verifiers: form.verifiers.filter((_, i) => i !== index),
  });

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.setAuth(gateway.id, {
        auth_mode: form.auth_mode,
        idp: form.auth_mode === "oauth" && form.idp ? form.idp : null,
        client_id: form.client_id || null,
        // Blank means "keep the stored secret": the UI never receives it back.
        client_secret: form.client_secret || null,
        config_url: form.config_url || null,
        required_scopes: form.required_scopes.split(/\s+/).filter(Boolean),
        allow_api_key: form.auth_mode === "oauth" && form.allow_api_key,
        verifiers: form.auth_mode === "oauth"
          ? form.verifiers
              .filter((v) => v.label && v.issuer && v.jwks_uri)
              .map((v) => ({ ...v, audience: v.audience || "" }))
          : [],
        // Blank means "keep the stored upstream token", same as the secret.
        upstream_token: form.upstream_token || null,
      });
      setForm({ ...form, client_secret: "" });
      onSaved({ kind: "ok", text: t("Auth configuration saved.") });
    } catch (err) {
      onSaved({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card flat">
      <fieldset className="form-section">
        <legend>{t("Who may call it")}</legend>
        <div className="field">
          <label htmlFor="auth-mode">{t("Mode")}</label>
          <select id="auth-mode" value={form.auth_mode} onChange={set("auth_mode")}>
            <option value="none">{t("Anyone (no auth)")}</option>
            <option value="api_key">{t("API key")}</option>
            <option value="oauth">{t("SSO (OAuth)")}</option>
          </select>
        </div>
      </fieldset>

      {form.auth_mode === "oauth" && (
        <fieldset className="form-section">
          <legend>{t("Sign-in flow")}</legend>
          <div className="field">
            <label htmlFor="auth-idp">{t("Identity provider")}</label>
            <select id="auth-idp" value={form.idp} onChange={set("idp")}>
              <option value="">{t("Choose…")}</option>
              <option value="github">GitHub</option>
              <option value="google">Google</option>
              <option value="azure">Microsoft</option>
              <option value="discord">Discord</option>
              <option value="oidc">{t("Custom OIDC (Auth0, Keycloak…)")}</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="auth-client-id">{t("Client ID")}</label>
            <input id="auth-client-id" value={form.client_id} onChange={set("client_id")}
                   placeholder="Ov23li…" className="mono" />
          </div>
          <div className="field">
            <label htmlFor="auth-client-secret">{t("Client secret")}</label>
            <input id="auth-client-secret" type="password" value={form.client_secret} onChange={set("client_secret")}
                   placeholder={t("leave blank to keep current")} className="mono" />
          </div>
          {form.idp === "oidc" && (
            <div className="field">
              <label htmlFor="auth-config-url">{t("Discovery URL")}</label>
              <input id="auth-config-url" value={form.config_url} onChange={set("config_url")}
                     placeholder="https://idp.example.com/.well-known/openid-configuration"
                     className="mono" />
            </div>
          )}
          <div className="field">
            <label htmlFor="auth-scopes">{t("Required scopes")}</label>
            <input id="auth-scopes" value={form.required_scopes} onChange={set("required_scopes")}
                   placeholder="read:user" className="mono" />
          </div>
        </fieldset>
      )}

      {form.auth_mode === "oauth" && (
        <fieldset className="form-section">
          <legend>{t("Other ways in")}</legend>
          <label className="permission" htmlFor="auth-allow-key">
            <input id="auth-allow-key" type="checkbox" checked={form.allow_api_key}
                   onChange={(event) => setForm({ ...form, allow_api_key: event.target.checked })} />
            <span>{t("Also accept API keys on this gateway")}</span>
          </label>
          <div className="hint" style={{ margin: "8px 0 var(--s4)" }}>
            {t("A browser client logs in, a script presents a key — same URL, same allowlist, same rate limit. The gateway needs at least one issued key.")}
          </div>

          {form.verifiers.map((verifier, index) => (
            <div key={index} className="verifier">
              <div className="verifier-head">
                <span className="eyebrow">{t("Issuer {n}", { n: index + 1 })}</span>
                <button type="button" className="ghost sm" onClick={() => removeVerifier(index)}>
                  {t("Remove")}
                </button>
              </div>
              <div className="field-row">
                <div className="field">
                  <label htmlFor={`verifier-label-${index}`}>{t("Label")}</label>
                  <input id={`verifier-label-${index}`} value={verifier.label}
                         onChange={setVerifier(index, "label")} placeholder="corp" />
                </div>
                <div className="field">
                  <label htmlFor={`verifier-issuer-${index}`}>{t("Issuer")}</label>
                  <input id={`verifier-issuer-${index}`} value={verifier.issuer}
                         onChange={setVerifier(index, "issuer")}
                         placeholder="https://idp.example.com/" className="mono" />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label htmlFor={`verifier-audience-${index}`}>{t("Audience (optional)")}</label>
                  <input id={`verifier-audience-${index}`} value={verifier.audience}
                         onChange={setVerifier(index, "audience")}
                         placeholder="omg" className="mono" />
                </div>
                <div className="field">
                  <label htmlFor={`verifier-jwks-${index}`}>{t("JWKS URL")}</label>
                  <input id={`verifier-jwks-${index}`} value={verifier.jwks_uri}
                         onChange={setVerifier(index, "jwks_uri")}
                         placeholder="https://idp.example.com/.well-known/jwks.json" className="mono" />
                </div>
              </div>
            </div>
          ))}
          <button type="button" className="ghost sm" onClick={addVerifier}>
            {t("Add issuer")}
          </button>
          <div className="hint" style={{ marginTop: 8 }}>
            {t("No login flow for these: the client already holds the token, and the gateway checks its signature, issuer and audience. Use it for Auth0, Keycloak, or a second IdP beside the one above.")}
          </div>
        </fieldset>
      )}

      <fieldset className="form-section">
        <legend>{t("Upstream")}</legend>
        <div className="field">
          <label htmlFor="auth-upstream">{t("Upstream token")}</label>
          <input id="auth-upstream" type="password" value={form.upstream_token} className="mono"
                 onChange={set("upstream_token")}
                 placeholder={t("only if the MCP server requires its own bearer")} />
          <div className="hint">
            {t("Sent as")} <span className="mono">Authorization: Bearer …</span>{" "}
            {t("on every upstream call, so callers never hold it. Leave blank to keep the current one.")}
          </div>
        </div>
      </fieldset>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {busy ? t("Saving…") : t("Save auth")}
        </button>
      </div>
    </form>
  );
}

/**
 * The limit, inline in the endpoint card.
 *
 * It was a card of its own, with one number in it — a whole surface for a value
 * that belongs to the same sentence as "what this gateway accepts". Here it is a
 * field, and the hint that explains what a request costs sits beside it.
 */
export function RateLimitForm({ gateway, onSaved }) {
  const t = useT();
  const [value, setValue] = useState(gateway.requests_per_minute ?? 0);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="limit-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await api.setRateLimit(gateway.id, Number(value) || 0);
          onSaved();
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="field inline">
        <label htmlFor="rate-limit">{t("Requests / minute")}</label>
        <input id="rate-limit" type="number" min="0" value={value}
               onChange={(event) => setValue(event.target.value)} />
      </div>
      <button className="primary sm" disabled={busy}>
        {busy ? t("Saving…") : t("Save limit")}
      </button>
      <p className="hint">
        {t("0 means no limit. Each subject gets its own budget, and only JSON-RPC requests count — handshakes and notifications are free.")}
      </p>
    </form>
  );
}

/**
 * The month's ceiling, next to the minute's.
 *
 * Two forms rather than one with two fields: they answer different questions —
 * "is this integration polite" and "is this integration over its bill" — and a
 * combined form would save one click at the cost of one clear sentence each.
 */
export function QuotaForm({ gateway, onSaved }) {
  const t = useT();
  const [value, setValue] = useState(gateway.monthly_quota ?? 0);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="limit-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await api.setQuota(gateway.id, Number(value) || 0);
          onSaved();
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="field inline">
        <label htmlFor="monthly-quota">{t("Calls / month")}</label>
        <input id="monthly-quota" type="number" min="0" value={value}
               onChange={(event) => setValue(event.target.value)} />
      </div>
      <button className="primary sm" disabled={busy}>
        {busy ? t("Saving…") : t("Save quota")}
      </button>
      <p className="hint">
        {t("0 means no ceiling. The counter resets on the first day of the month, UTC, and a call over the quota is refused with 429 before it reaches the upstream.")}
      </p>
    </form>
  );
}

/**
 * This gateway's slice of the request log, with the same affordances as the
 * global one: a cursor, a filter under every column, sortable headers, and a
 * drawer for the row that needs more than a line.
 *
 * A separate table rather than a link to `/traffic`: the question "what did this
 * gateway just do" is asked *here*, and sending the reader to another page to
 * retype the filter is how a detail page stops being a page.
 */
export function Requests({ gateway }) {
  const t = useT();
  const { locale } = useI18n();
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("occurred_at", SORTS);
  const [open, setOpen] = useState(null);

  const { items, next, error, busy, more } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.requests({ limit, before, gateway_id: gateway.id, ...query });
      return { items: page.requests, next: page.next };
    },
    { ...filters, ...sort },
  );

  return (
    <>
      <Skeleton
        data={items}
        sample={SAMPLE_REQUESTS}
        label={t("Loading recent requests")}
        empty={items?.length === 0}
        emptyMessage={
          <NoMatches
            filters={filters}
            onClear={() => setFilters({})}
            title={t("No calls yet")}
          >
            {t("Requests appear here as they arrive.")}
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
                  <th>{t("Tool")}</th>
                  <th>{t("IP")}</th>
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
                    <td className="mono">{request.subject ?? "—"}</td>
                    <td>
                      <Badge kind={outcomeKind(request.outcome)}>
                        {outcomeLabel(t, request.outcome)}
                      </Badge>
                    </td>
                    <td className="num">{request.status}</td>
                    <td className="num">{request.duration_ms} ms</td>
                    <td className="mono hint">{request.tool || "—"}</td>
                    <td className="mono hint">{request.ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Skeleton>
      <LoadMore next={next} busy={busy} onClick={more} />
      {error ? <p className="hint" style={{ marginBottom: 0 }}>{error}</p> : null}

      {open && (
        <Drawer
          title={outcomeLabel(t, open.outcome)}
          sub={open.event_id}
          onClose={() => setOpen(null)}
          actions={
            <button className="ghost sm" onClick={() => setOpen(null)}>
              {t("Close")}
            </button>
          }
        >
          <Details
            rows={[
              [t("When"), new Date(open.occurred_at).toLocaleString(locale)],
              [t("Seen"), relative(open.occurred_at)],
              [t("Subject"), <span className="mono">{open.subject ?? t("anonymous")}</span>],
              [
                t("Outcome"),
                <Badge kind={outcomeKind(open.outcome)}>
                  {outcomeLabel(t, open.outcome)}
                </Badge>,
              ],
              [t("Status"), open.status],
              [t("Latency"), `${open.duration_ms} ms`],
              [t("Method"), <span className="mono">{open.method}</span>],
              [t("Tool"), <span className="mono">{open.tool || "—"}</span>],
              [t("IP"), <span className="mono">{open.ip || "—"}</span>],
              // Only when there is something to say: an empty row on every
              // allowed call would train the reader to skip the section.
              // The server stores an English sentence, not a code, so the log is
              // readable from `psql` too. Translating it here keeps that choice
              // from making the panel English-only.
              ...(open.reason ? [[t("Reason"), t(open.reason)]] : []),
              // Blank on rows written before the header existed, so it is shown
              // only when there is something to correlate with.
              ...(open.request_id
                ? [[t("Request id"), <span className="mono">{open.request_id}</span>]]
                : []),
              [t("Event id"), <span className="mono">{open.event_id}</span>],
            ]}
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

export function Traffic({ stats }) {
  const t = useT();
  return (
    <Card title={t("Traffic")}>
      <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading the chart")}>
        {(data) => <Chart series={data.series} />}
      </Skeleton>
      <div className="grid-2" style={{ marginTop: 16 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>{t("By outcome")}</div>
          <Skeleton
            data={stats}
            sample={SAMPLE_STATS}
            label={t("Loading outcomes")}
            empty={stats && Object.keys(stats.by_outcome ?? {}).length === 0}
            emptyMessage={<Empty>{t("Nothing yet.")}</Empty>}
          >
            {(data) => (
              <table>
                <tbody>
                  {Object.entries(data.by_outcome ?? {}).map(([outcome, count]) => (
                    <tr key={outcome}>
                      <td>
                        <Badge kind={outcomeKind(outcome)}>
                          {outcomeLabel(t, outcome)}
                        </Badge>
                      </td>
                      <td className="num">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Skeleton>
        </div>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>{t("By caller IP")}</div>
          <Skeleton
            data={stats}
            sample={SAMPLE_STATS}
            label={t("Loading callers")}
            empty={stats && Object.keys(stats.by_ip ?? {}).length === 0}
            emptyMessage={<Empty>{t("Nothing yet.")}</Empty>}
          >
            {(data) => (
              <table>
                <tbody>
                  {Object.entries(data.by_ip ?? {}).slice(0, 6).map(([ip, count]) => (
                    <tr key={ip}>
                      <td className="mono">{ip || t("unknown")}</td>
                      <td className="num">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Skeleton>
        </div>
      </div>
    </Card>
  );
}
