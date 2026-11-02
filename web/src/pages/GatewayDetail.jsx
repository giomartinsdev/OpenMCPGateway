import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Empty, Endpoint, Metric, Notice, can, relative } from "../ui.jsx";
import { navigate } from "../App.jsx";
import { ConnectGuide } from "./gateway/ConnectGuide.jsx";
import {
  AuthForm, RateLimitForm, RecentRequests, Traffic,
} from "./gateway/Panels.jsx";

export function GatewayDetail({ id, me }) {
  // Two separate permissions: managing a gateway is not the same as minting a
  // credential for it, and the server checks them separately.
  const canEdit = can(me, "gateways:write");
  const canKeys = can(me, "keys:write");
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

      <ConnectGuide gateway={gateway} />

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
                const subject = window.prompt("Subject to allow (e.g. octocat):");
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
          actions={canKeys && (
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
                    {canKeys && (
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

      <Traffic stats={stats} />

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
