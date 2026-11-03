import { useEffect, useState } from "react";
import { api } from "../api.js";
import {
  Badge, Bone, Card, Empty, Endpoint, Notice, Skeleton, Stat, can, relative,
} from "../ui.jsx";
import { navigate } from "../App.jsx";
import { ConnectGuide } from "./gateway/ConnectGuide.jsx";
import {
  AuthForm, RateLimitForm, RecentRequests, Traffic,
} from "./gateway/Panels.jsx";

// Placeholder for the detail page: one object with the shape the page reads, so
// the skeleton is the page's own layout rather than a grey rectangle.
const SAMPLE = {
  id: "gw_0000",
  name: "Gateway name",
  slug: "slug",
  description: "What this server is for",
  endpoint: "https://gateway.example.com/g/slug/mcp",
  target_url: "http://upstream.svc.cluster.local:8000/mcp",
  auth_mode: "oauth",
  idp: "google",
  enabled: true,
  requests_per_minute: 120,
  client_id: "…apps.googleusercontent.com",
  principals: [{ subject: "someone@example.com", added_at: new Date().toISOString() }],
  api_keys: [{ label: "default", key_id: "key_0000", issued_at: new Date().toISOString() }],
  requests: [],
  stats: { requests: 0, avg_duration_ms: 0, max_duration_ms: 0, by_ip: {}, series: [] },
};

export function GatewayDetail({ id, me }) {
  // Two separate permissions: managing a gateway is not the same as minting a
  // credential for it, and the server checks them separately.
  const canEdit = can(me, "gateways:write");
  const canKeys = can(me, "keys:write");
  const [gateway, setGateway] = useState(null);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null); // {kind, text}
  // Holds a plaintext only between the response and the operator's clipboard.
  const [newKey, setNewKey] = useState("");

  async function load() {
    try {
      setGateway(await api.gateway(id));
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [id]);

  if (error) return <Notice kind="err">{error}</Notice>;

  const stats = gateway?.stats ?? {};
  const header = gateway ?? SAMPLE;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <a href="#/">Gateways</a>
            <span className="sep">/</span>
            <span>{header.slug}</span>
          </div>
          <Skeleton data={gateway} sample={SAMPLE} label="Loading gateway">
            {(data) => <h1>{data.name}</h1>}
          </Skeleton>
          <div className="sub">{header.description || "No description."}</div>
        </div>
        <div className="actions">
          <Badge kind={header.enabled ? "ok" : "mute"}>
            {header.enabled ? "enabled" : "disabled"}
          </Badge>
          {gateway && canEdit && (
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

      {banner && <Notice kind={banner.kind}>{banner.text}</Notice>}

      {gateway?.pending && (
        <Notice kind="err">
          This gateway is enabled but not serving yet: {gateway.pending}. Set the OAuth
          client details below and it goes live immediately.
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 12 }}>
        <Skeleton data={gateway} sample={SAMPLE} label="Loading requests">
          {(data) => <Stat label="Requests / 60 min" value={data.stats?.requests ?? 0} />}
        </Skeleton>
        <Skeleton data={gateway} sample={SAMPLE} label="Loading latency">
          {(data) => (
            <Stat
              label="Avg latency"
              value={`${data.stats?.avg_duration_ms ?? 0} ms`}
              hint={`peak ${data.stats?.max_duration_ms ?? 0} ms`}
            />
          )}
        </Skeleton>
        <Skeleton data={gateway} sample={SAMPLE} label="Loading callers">
          {(data) => (
            <Stat label="Distinct callers" value={Object.keys(data.stats?.by_ip ?? {}).length} />
          )}
        </Skeleton>
      </div>

      <Card title="Endpoint">
        {gateway ? (
          <>
            <Endpoint value={gateway.endpoint} />
            <p className="hint" style={{ marginTop: 10 }}>
              Point an MCP client here. Upstream stays private at{" "}
              <span className="mono">{gateway.target_url}</span>.
            </p>
          </>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            <Bone height={38} radius={6} />
            <Bone height={12} width="70%" />
          </div>
        )}
      </Card>

      {gateway ? <ConnectGuide gateway={gateway} /> : null}

      <Card title="Auth">
        <div className="grid-2">
          <div>
            <div className="row" style={{ gap: 24, marginBottom: 12 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>Mode</div>
                <Badge kind={header.auth_mode === "none" ? "warn" : "info"}>
                  {header.auth_mode}
                  {header.idp ? ` · ${header.idp}` : ""}
                </Badge>
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>Allowlist</div>
                <Badge kind={header.principals.length ? "ok" : "mute"}>
                  {header.principals.length
                    ? `${header.principals.length} principals`
                    : "any authenticated subject"}
                </Badge>
              </div>
            </div>
            <p className="hint" style={{ margin: 0 }}>
              An allowlist is a second gate: a valid token still has to belong to a listed
              subject. Empty means "anyone who authenticates".
            </p>
          </div>
          {gateway ? (
            canEdit ? (
              <AuthForm gateway={gateway} onSaved={(msg) => { setBanner(msg); load(); }} />
            ) : (
              <Notice kind="info">
                Read-only: an administrator of this tenant changes the auth.
              </Notice>
            )
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              <Bone height={34} />
              <Bone height={34} />
              <Bone height={34} />
            </div>
          )}
        </div>
      </Card>

      <Card title="Rate limit">
        {gateway ? (
          canEdit ? (
            <RateLimitForm gateway={gateway} onSaved={load} />
          ) : (
            <Notice kind="info">
              {gateway.requests_per_minute
                ? `${gateway.requests_per_minute} requests per minute per caller.`
                : "No limit. Each caller gets its own budget."}
            </Notice>
          )
        ) : (
          <Bone height={34} width={320} />
        )}
      </Card>

      <div className="grid-2" style={{ marginTop: 12 }}>
        <Card
          title="Allowed subjects"
          actions={gateway && canEdit && (
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
          {header.principals.length === 0 ? (
            gateway ? (
              <Empty title="No allowlist">Every authenticated caller is accepted.</Empty>
            ) : (
              <Bone height={60} radius={10} />
            )
          ) : (
            <table>
              <tbody>
                {header.principals.map((principal) => (
                  <tr key={principal.subject}>
                    <td className="mono">{principal.subject}</td>
                    <td className="num hint">{relative(principal.added_at)}</td>
                    {canEdit && (
                      <td className="actions">
                        <button
                          className="ghost sm"
                          onClick={() => api.removePrincipal(id, principal.subject).then(load)}
                          disabled={!gateway}
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
          actions={gateway && canKeys && (
            <button
              className="sm"
              onClick={() => {
                api.issueKey(id, "default")
                  .then((issued) => {
                    setNewKey(issued.api_key);
                    load();
                  })
                  .catch((e) => setError(e.message));
              }}
            >
              Issue key
            </button>
          )}
        >
          {/* The plaintext exists once, in the response that minted it. It is
              shown here — next to the button that produced it — because a banner
              at the top of a long page is a secret people scroll past. */}
          {newKey && (
            <div className="secret" style={{ marginBottom: 12 }}>
              <div className="spread" style={{ marginBottom: 6 }}>
                <strong>Copy this key now — it is not stored and cannot be shown again.</strong>
                <button className="ghost sm" onClick={() => setNewKey(null)}>dismiss</button>
              </div>
              <div className="row">
                <span className="mono" style={{ wordBreak: "break-all" }}>{newKey}</span>
                <button
                  className="ghost copy"
                  onClick={() => navigator.clipboard?.writeText(newKey)}
                >
                  copy
                </button>
              </div>
            </div>
          )}
          {header.api_keys.length === 0 ? (
            gateway ? (
              <Empty title="No keys">Issue one to authenticate with a bearer token.</Empty>
            ) : (
              <Bone height={60} radius={10} />
            )
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Key id</th>
                  <th className="num">Issued</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {header.api_keys.map((key) => (
                  <tr key={key.key_id}>
                    <td>{key.label}</td>
                    {/* The id, not the subject: the subject is `api-key:<id>`
                        and pasting that as a bearer is the mistake this column
                        used to invite. */}
                    <td className="mono hint">{key.key_id}</td>
                    <td className="num hint">{relative(key.issued_at)}</td>
                    {canKeys && (
                      <td className="actions">
                        <button
                          className="ghost sm"
                          disabled={!gateway}
                          onClick={() => {
                            if (!window.confirm(
                              `Rotate ${key.label}? The current key stops working immediately.`
                            )) return;
                            api.rotateKey(id, key.key_id)
                              .then((rotated) => {
                                setNewKey(rotated.api_key);
                                load();
                              })
                              .catch((e) => setError(e.message));
                          }}
                        >
                          rotate
                        </button>
                        <button
                          className="ghost sm danger"
                          disabled={!gateway}
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
        <RecentRequests requests={gateway ? gateway.requests : null} />
      </Card>

      {gateway && canEdit && (
        <Card title="Danger zone" className="danger-zone">
          <div className="spread">
            <div>
              <h3>Delete this gateway</h3>
              <div className="hint" style={{ marginTop: 2 }}>
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
