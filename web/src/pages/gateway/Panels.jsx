// The gateway detail page's pieces. Kept out of the page file so each one is a
// single concern: auth, rate limit, the traffic tables, and the copy-paste guide.

import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { Badge, Card, Chart, Empty, outcomeKind, relative } from "../../ui.jsx";

export const OUTCOME_LABEL = {
  allowed: "allowed",
  unauthenticated: "401 unauthenticated",
  forbidden: "403 forbidden",
  rate_limited: "429 rate limited",
};

export function AuthForm({ gateway, onSaved }) {
  const [form, setForm] = useState({
    auth_mode: gateway.auth_mode,
    idp: gateway.idp ?? "",
    client_id: gateway.client_id ?? "",
    client_secret: "",
    config_url: gateway.config_url ?? "",
    required_scopes: (gateway.required_scopes ?? []).join(" "),
    upstream_token: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

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
        // Blank means "keep the stored upstream token", same as the secret.
        upstream_token: form.upstream_token || null,
      });
      setForm({ ...form, client_secret: "" });
      onSaved({ kind: "ok", text: "Auth configuration saved." });
    } catch (err) {
      onSaved({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card flat">
      <div className="field">
        <label htmlFor="auth-mode">Mode</label>
        <select id="auth-mode" value={form.auth_mode} onChange={set("auth_mode")}>
          <option value="none">Anyone (no auth)</option>
          <option value="api_key">API key</option>
          <option value="oauth">SSO (OAuth)</option>
        </select>
      </div>
      {form.auth_mode === "oauth" && (
        <>
          <div className="field">
            <label htmlFor="auth-idp">Identity provider</label>
            <select id="auth-idp" value={form.idp} onChange={set("idp")}>
              <option value="">Choose…</option>
              <option value="github">GitHub</option>
              <option value="google">Google</option>
              <option value="azure">Microsoft</option>
              <option value="discord">Discord</option>
              <option value="oidc">Custom OIDC (Auth0, Keycloak…)</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="auth-client-id">Client ID</label>
            <input id="auth-client-id" value={form.client_id} onChange={set("client_id")}
                   placeholder="Ov23li…" className="mono" />
          </div>
          <div className="field">
            <label htmlFor="auth-client-secret">Client secret</label>
            <input id="auth-client-secret" type="password" value={form.client_secret} onChange={set("client_secret")}
                   placeholder="leave blank to keep current" className="mono" />
          </div>
          {form.idp === "oidc" && (
            <div className="field">
              <label htmlFor="auth-config-url">Discovery URL</label>
              <input id="auth-config-url" value={form.config_url} onChange={set("config_url")}
                     placeholder="https://idp.example.com/.well-known/openid-configuration"
                     className="mono" />
            </div>
          )}
          <div className="field">
            <label htmlFor="auth-scopes">Required scopes</label>
            <input id="auth-scopes" value={form.required_scopes} onChange={set("required_scopes")}
                   placeholder="read:user" className="mono" />
          </div>
        </>
      )}
      <div className="field" style={{ marginTop: 4 }}>
        <label htmlFor="auth-upstream">Upstream token</label>
        <input id="auth-upstream" type="password" value={form.upstream_token} className="mono"
               onChange={set("upstream_token")}
               placeholder="only if the MCP server requires its own bearer" />
        <div className="hint" style={{ marginTop: 4 }}>
          Sent as <span className="mono">Authorization: Bearer …</span> on every
          upstream call, so callers never hold it. Leave blank to keep the current one.
        </div>
      </div>
      <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
    </form>
  );
}

export function RateLimitForm({ gateway, onSaved }) {
  const [value, setValue] = useState(gateway.requests_per_minute ?? 0);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="row"
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
      <div style={{ maxWidth: 200 }}>
        <label htmlFor="rate-limit">Requests per minute</label>
        <input id="rate-limit" type="number" min="0" value={value}
               onChange={(e) => setValue(e.target.value)} />
      </div>
      <button className="primary" disabled={busy} style={{ alignSelf: "flex-end" }}>
        {busy ? "Saving…" : "Save"}
      </button>
      <p className="hint" style={{ margin: 0, flex: 1, minWidth: 240 }}>
        0 means no limit. Each subject gets its own budget, and only JSON-RPC
        requests count — handshakes and notifications are free.
      </p>
    </form>
  );
}

export function RecentRequests({ requests }) {
  if (!requests?.length) return <Empty>No calls recorded yet.</Empty>;
  return (
    <table>
      <thead>
        <tr>
          <th>When</th>
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
            <td className="mono" style={{ fontSize: 11.5 }}>{request.subject ?? "—"}</td>
            <td>
              <Badge kind={outcomeKind(request.outcome)}>
                {OUTCOME_LABEL[request.outcome] ?? request.outcome}
              </Badge>
            </td>
            <td className="num">{request.status}</td>
            <td className="num">{request.duration_ms} ms</td>
            <td className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
              {request.ip || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Traffic({ stats }) {
  return (
    <Card title="Traffic">
      <Chart series={stats.series} />
      <div className="grid-2" style={{ marginTop: 14 }}>
        <div>
          <div className="metric-label" style={{ marginBottom: 8 }}>By outcome</div>
          {Object.entries(stats.by_outcome ?? {}).length === 0 ? (
            <div className="hint">Nothing yet.</div>
          ) : (
            Object.entries(stats.by_outcome).map(([outcome, count]) => (
              <div key={outcome} className="spread" style={{ padding: "4px 0" }}>
                <Badge kind={outcomeKind(outcome)}>{OUTCOME_LABEL[outcome] ?? outcome}</Badge>
                <span className="mono">{count}</span>
              </div>
            ))
          )}
        </div>
        <div>
          <div className="metric-label" style={{ marginBottom: 8 }}>By caller IP</div>
          {Object.entries(stats.by_ip ?? {}).length === 0 ? (
            <div className="hint">Nothing yet.</div>
          ) : (
            Object.entries(stats.by_ip).slice(0, 6).map(([ip, count]) => (
              <div key={ip} className="spread" style={{ padding: "4px 0" }}>
                <span className="mono" style={{ fontSize: 11.5 }}>{ip || "unknown"}</span>
                <span className="mono">{count}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </Card>
  );
}
