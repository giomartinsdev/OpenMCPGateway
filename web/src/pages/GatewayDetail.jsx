import { useEffect, useState } from "react";
import { api } from "../api.js";
import {
  Badge, Card, Chart, Empty, Endpoint, Metric, Notice, outcomeKind, relative,
} from "../ui.jsx";
import { navigate } from "../App.jsx";

const OUTCOME_LABEL = {
  allowed: "allowed",
  unauthenticated: "401 unauthenticated",
  forbidden: "403 forbidden",
  rate_limited: "429 rate limited",
};

export function GatewayDetail({ id, me }) {
  const canEdit = me?.is_admin === true;
  const [gateway, setGateway] = useState(null);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null); // {kind, text, secret?}

  async function load() {
    try {
      setGateway(await api.gateway(id));
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [id]);

  if (error) return <Notice kind="err">{error}</Notice>;
  if (!gateway) return <div className="hint">Loading…</div>;

  const stats = gateway.stats ?? {};

  return (
    <>
      <div className="topbar">
        <div>
          <div className="hint" style={{ marginBottom: 4 }}>
            <a href="#/">Gateways</a> / {gateway.slug}
          </div>
          <h1>{gateway.name}</h1>
          <div className="sub">{gateway.description || "No description."}</div>
        </div>
        <div className="row">
          <Badge kind={gateway.enabled ? "ok" : "mute"}>
            {gateway.enabled ? "enabled" : "disabled"}
          </Badge>
          {canEdit && (
            <button
              onClick={async () => {
                await (gateway.enabled ? api.disable(id) : api.enable(id));
                load();
              }}
            >
              {gateway.enabled ? "Disable" : "Enable"}
            </button>
          )}
        </div>
      </div>

      {banner && (
        <Notice kind={banner.kind}>
          {banner.text}
          {banner.secret && <div className="secret">{banner.secret}</div>}
        </Notice>
      )}

      {gateway.pending && (
        <Notice kind="err">
          This gateway is enabled but not serving yet: {gateway.pending}. Set the OAuth
          client details below and it goes live immediately.
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 14 }}>
        <Metric label="Requests / 60 min" value={stats.requests ?? 0} />
        <Metric label="Avg latency" value={`${stats.avg_duration_ms ?? 0} ms`}
                hint={`peak ${stats.max_duration_ms ?? 0} ms`} />
        <Metric label="Distinct callers" value={Object.keys(stats.by_ip ?? {}).length} />
      </div>

      <Card title="Endpoint">
        <Endpoint value={gateway.endpoint} />
        <p className="hint" style={{ marginBottom: 0 }}>
          Point an MCP client here. Upstream stays private at{" "}
          <span className="mono">{gateway.target_url}</span>.
        </p>
      </Card>

      <Card title="Auth">
        <div className="grid-2">
          <div>
            <div className="spread" style={{ marginBottom: 10 }}>
              <div>
                <div className="metric-label">Mode</div>
                <div style={{ marginTop: 4 }}>
                  <Badge kind={gateway.auth_mode === "none" ? "warn" : "info"}>
                    {gateway.auth_mode}
                    {gateway.idp ? ` · ${gateway.idp}` : ""}
                  </Badge>
                </div>
              </div>
              <div>
                <div className="metric-label">Allowlist</div>
                <div style={{ marginTop: 4 }}>
                  <Badge kind={gateway.principals.length ? "ok" : "mute"}>
                    {gateway.principals.length
                      ? `${gateway.principals.length} principals`
                      : "open to any authenticated subject"}
                  </Badge>
                </div>
              </div>
            </div>
            <p className="hint">
              An allowlist is a second gate: a valid token still has to belong to a
              listed subject. Empty means "anyone who authenticates".
            </p>
          </div>
          {canEdit ? (
            <AuthForm gateway={gateway} onSaved={(msg) => { setBanner(msg); load(); }} />
          ) : (
            <Notice kind="info">
              Read-only: an administrator of this tenant changes the auth.
            </Notice>
          )}
        </div>
      </Card>

      <Card title="Rate limit">
        {canEdit ? (
          <RateLimitForm gateway={gateway} onSaved={load} />
        ) : (
          <Notice kind="info">
            {gateway.requests_per_minute
              ? `${gateway.requests_per_minute} requests per minute per caller.`
              : "No limit. Each caller gets its own budget."}
          </Notice>
        )}
      </Card>

      <div className="grid-2">
        <Card
          title="Allowed subjects"
          actions={canEdit && (
            <button
              className="sm"
              onClick={() => {
                const subject = window.prompt("Subject to allow (e.g. github:octocat):");
                if (!subject) return;
                api.addPrincipal(id, subject).then(load).catch((e) => setError(e.message));
              }}
            >
              Add
            </button>
          )}
        >
          {gateway.principals.length === 0 ? (
            <Empty>No allowlist — every authenticated caller is accepted.</Empty>
          ) : (
            <table>
              <tbody>
                {gateway.principals.map((principal) => (
                  <tr key={principal.subject}>
                    <td className="mono">{principal.subject}</td>
                    <td className="hint">{relative(principal.added_at)}</td>
                    {canEdit && (
                      <td style={{ width: 1 }}>
                        <button
                          className="ghost sm"
                          onClick={() => api.removePrincipal(id, principal.subject).then(load)}
                        >
                          remove
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card
          title="API keys"
          actions={canEdit && (
            <button
              className="sm"
              onClick={() => {
                api.issueKey(id, "default")
                  .then((issued) => {
                    setBanner({
                      kind: "ok",
                      text: "Copy this key now — it is not stored and cannot be shown again.",
                      secret: issued.api_key,
                    });
                    load();
                  })
                  .catch((e) => setError(e.message));
              }}
            >
              Issue key
            </button>
          )}
        >
          {gateway.api_keys.length === 0 ? (
            <Empty>No keys. Issue one to authenticate with a bearer token.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Subject</th>
                  <th>Issued</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {gateway.api_keys.map((key) => (
                  <tr key={key.key_id}>
                    <td>{key.label}</td>
                    <td className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
                      {key.subject}
                    </td>
                    <td className="hint">{relative(key.issued_at)}</td>
                    {canEdit && (
                      <td style={{ width: 1 }}>
                        <button
                          className="ghost sm danger"
                          onClick={() => {
                            if (!window.confirm(`Revoke ${key.label}? Callers using it get 401 at once.`)) return;
                            api.revokeKey(id, key.key_id).then(load);
                          }}
                        >
                          revoke
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

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

      <Card title="Recent requests">
        <RecentRequests requests={gateway.requests} />
      </Card>

      {canEdit && (
      <Card title="Danger zone">
        <div className="spread">
          <div>
            <h3>Delete this gateway</h3>
            <div className="hint">
              Removes the route and its keys immediately. The event log keeps the history.
            </div>
          </div>
          <button
            className="danger"
            onClick={async () => {
              if (!window.confirm(`Delete ${gateway.slug}? Its route stops answering.`)) return;
              await api.deleteGateway(id);
              navigate("/");
            }}
          >
            Delete
          </button>
        </div>
      </Card>
      )}
    </>
  );
}

function AuthForm({ gateway, onSaved }) {
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
        <label>Mode</label>
        <select value={form.auth_mode} onChange={set("auth_mode")}>
          <option value="none">Anyone (no auth)</option>
          <option value="api_key">API key</option>
          <option value="oauth">SSO (OAuth)</option>
        </select>
      </div>
      {form.auth_mode === "oauth" && (
        <>
          <div className="field">
            <label>Identity provider</label>
            <select value={form.idp} onChange={set("idp")}>
              <option value="">Choose…</option>
              <option value="github">GitHub</option>
              <option value="google">Google</option>
              <option value="microsoft">Microsoft</option>
              <option value="oidc">Custom OIDC</option>
            </select>
          </div>
          <div className="field">
            <label>Client ID</label>
            <input value={form.client_id} onChange={set("client_id")}
                   placeholder="Ov23li…" className="mono" />
          </div>
          <div className="field">
            <label>Client secret</label>
            <input type="password" value={form.client_secret} onChange={set("client_secret")}
                   placeholder="leave blank to keep current" className="mono" />
          </div>
          {form.idp === "oidc" && (
            <div className="field">
              <label>Discovery URL</label>
              <input value={form.config_url} onChange={set("config_url")}
                     placeholder="https://idp.example.com/.well-known/openid-configuration"
                     className="mono" />
            </div>
          )}
          <div className="field">
            <label>Required scopes</label>
            <input value={form.required_scopes} onChange={set("required_scopes")}
                   placeholder="read:user" className="mono" />
          </div>
        </>
      )}
      <div className="field" style={{ marginTop: 4 }}>
        <label>Upstream token</label>
        <input type="password" value={form.upstream_token} className="mono"
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

function RateLimitForm({ gateway, onSaved }) {
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
        <label>Requests per minute</label>
        <input type="number" min="0" value={value}
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

function RecentRequests({ requests }) {
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
