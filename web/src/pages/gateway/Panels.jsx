// The gateway detail page's pieces. Kept out of the page file so each one is a
// single concern: auth, rate limit, the traffic tables, and the copy-paste guide.

import { useState } from "react";
import { api } from "../../api.js";
import { useT } from "../../i18n.jsx";
import {
  Badge, Card, Chart, Empty, Skeleton, SAMPLE_REQUESTS, SAMPLE_STATS,
  outcomeKind, relative,
} from "../../ui.jsx";

// Translated at the point of render: the key is the English phrase, so a locale
// only has to supply the sentence, not a second naming scheme.
export const OUTCOME_LABEL = {
  allowed: "allowed",
  unauthenticated: "401 unauthenticated",
  forbidden: "403 forbidden",
  rate_limited: "429 rate limited",
};

export function AuthForm({ gateway, onSaved }) {
  const t = useT();
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
      onSaved({ kind: "ok", text: t("Auth configuration saved.") });
    } catch (err) {
      onSaved({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card flat">
      <div className="field">
        <label htmlFor="auth-mode">{t("Mode")}</label>
        <select id="auth-mode" value={form.auth_mode} onChange={set("auth_mode")}>
          <option value="none">{t("Anyone (no auth)")}</option>
          <option value="api_key">{t("API key")}</option>
          <option value="oauth">{t("SSO (OAuth)")}</option>
        </select>
      </div>
      {form.auth_mode === "oauth" && (
        <>
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
        </>
      )}
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
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {busy ? t("Saving…") : t("Save")}
        </button>
      </div>
    </form>
  );
}

export function RateLimitForm({ gateway, onSaved }) {
  const t = useT();
  const [value, setValue] = useState(gateway.requests_per_minute ?? 0);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="row"
      style={{ alignItems: "flex-end" }}
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
      <div className="field inline" style={{ width: 180 }}>
        <label htmlFor="rate-limit">{t("Requests / minute")}</label>
        <input id="rate-limit" type="number" min="0" value={value}
               onChange={(e) => setValue(e.target.value)} />
      </div>
      <button className="primary" disabled={busy}>
        {busy ? t("Saving…") : t("Save")}
      </button>
      <p className="hint" style={{ margin: 0, flex: 1, minWidth: 240 }}>
        {t("0 means no limit. Each subject gets its own budget, and only JSON-RPC requests count — handshakes and notifications are free.")}
      </p>
    </form>
  );
}

export function RecentRequests({ requests }) {
  const t = useT();
  return (
    <Skeleton
      data={requests}
      sample={SAMPLE_REQUESTS}
      label={t("Loading recent requests")}
      empty={requests?.length === 0}
      emptyMessage={
        <Empty title={t("No calls yet")}>{t("Requests appear here as they arrive.")}</Empty>
      }
    >
      {(rows) => (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("When")}</th>
                <th>{t("Subject")}</th>
                <th>{t("Outcome")}</th>
                <th className="num">{t("Status")}</th>
                <th className="num">{t("Latency")}</th>
                <th>{t("IP")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((request, index) => (
                <tr key={`${request.occurred_at}-${index}`}>
                  <td className="hint nowrap">{relative(request.occurred_at)}</td>
                  <td className="mono">{request.subject ?? "—"}</td>
                  <td>
                    <Badge kind={outcomeKind(request.outcome)}>
                      {t(OUTCOME_LABEL[request.outcome] ?? request.outcome)}
                    </Badge>
                  </td>
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
                          {t(OUTCOME_LABEL[outcome] ?? outcome)}
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
