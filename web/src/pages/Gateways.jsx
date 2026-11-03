import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Empty, Metric, Notice, can, relative } from "../ui.jsx";
import { navigate } from "../App.jsx";

const AUTH_LABEL = { none: "open", api_key: "API key", oauth: "SSO" };

export function Gateways({ me }) {
  const canEdit = can(me, "gateways:write");
  const [state, setState] = useState({ loading: true, gateways: [], stats: null });
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const [{ gateways }, stats] = await Promise.all([api.gateways(), api.stats(null, 60)]);
      setState({ loading: false, gateways, stats });
    } catch (err) {
      setError(err.message);
      setState({ loading: false, gateways: [], stats: null });
    }
  }

  useEffect(() => { load(); }, []);

  if (state.loading) return <div className="hint">Loading…</div>;

  const live = state.gateways.filter((g) => g.enabled);
  const open = state.gateways.filter((g) => g.auth_mode === "none");

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Gateways</h1>
          <div className="sub">
            Every MCP server you front, with its own auth, limits and traffic.
          </div>
        </div>
        {canEdit && (
          <button className="primary" onClick={() => setCreating(true)}>
            New gateway
          </button>
        )}
      </div>

      <Notice kind="err">{error}</Notice>

      {open.length > 0 && (
        <Notice kind="info">
          {open.length === 1 ? "1 gateway is" : `${open.length} gateways are`} proxying with no
          authentication. That is fine on a private network; require a key or SSO before
          exposing them.
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 14 }}>
        <Metric label="Gateways" value={state.gateways.length} hint={`${live.length} enabled`} />
        <Metric
          label="Requests / 60 min"
          value={state.stats?.requests ?? 0}
          hint={`avg ${state.stats?.avg_duration_ms ?? 0} ms`}
        />
        <Metric
          label="Rejected"
          value={
            (state.stats?.by_outcome?.unauthenticated ?? 0) +
            (state.stats?.by_outcome?.forbidden ?? 0) +
            (state.stats?.by_outcome?.rate_limited ?? 0)
          }
          hint="401 / 403 / 429"
        />
      </div>

      {creating && (
        <CreateGateway
          onCancel={() => setCreating(false)}
          // Straight to the new gateway: its endpoint and key are the next
          // thing anyone needs, and the list only repeats the name.
          onCreated={(id) => { setCreating(false); navigate(`/gateways/${id}`); }}
        />
      )}

      <Card title="All gateways">
        {state.gateways.length === 0 ? (
          <Empty>No gateways yet. Create one to expose an MCP server.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Endpoint</th>
                <th>Auth</th>
                <th className="num">Limit</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {state.gateways.map((gateway) => (
                <tr
                  key={gateway.id}
                  className="clickable"
                  onClick={() => navigate(`/gateways/${gateway.id}`)}
                >
                  <td>
                    <div style={{ color: "var(--fg)", fontWeight: 500 }}>
                      {gateway.name}
                    </div>
                    <div className="hint mono">{gateway.slug}</div>
                  </td>
                  <td className="mono" style={{ fontSize: 11.5, color: "var(--fg-4)" }}>
                    {gateway.endpoint}
                  </td>
                  <td>
                    <Badge kind={gateway.auth_mode === "none" ? "warn" : "info"}>
                      {AUTH_LABEL[gateway.auth_mode] ?? gateway.auth_mode}
                      {gateway.idp ? ` · ${gateway.idp}` : ""}
                    </Badge>
                  </td>
                  <td className="num">
                    {gateway.requests_per_minute
                      ? `${gateway.requests_per_minute}/min`
                      : "—"}
                  </td>
                  <td>
                    <Badge kind={gateway.enabled ? "ok" : "mute"}>
                      {gateway.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </td>
                  <td className="hint">{relative(gateway.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function CreateGateway({ onCancel, onCreated }) {
  const [form, setForm] = useState({
    slug: "",
    name: "",
    target_url: "",
    description: "",
    auth_mode: "api_key",
    idp: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { id } = await api.createGateway({
        ...form,
        idp: form.auth_mode === "oauth" && form.idp ? form.idp : null,
      });
      onCreated(id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="New gateway"
      actions={
        <button className="ghost sm" onClick={onCancel}>
          Cancel
        </button>
      }
    >
      <form onSubmit={submit}>
        <Notice kind="err">{error}</Notice>
        <div className="field-row">
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" value={form.name} onChange={set("name")} required
                   placeholder="Rancher cluster" />
          </div>
          <div className="field">
            <label htmlFor="slug">Slug</label>
            <input id="slug" value={form.slug} onChange={set("slug")} required
                   pattern="[a-z0-9-]+" placeholder="rancher" />
            <div className="hint" style={{ marginTop: 4 }}>
              Becomes <span className="mono">/g/{form.slug || "slug"}/mcp</span>
            </div>
          </div>
        </div>
        <div className="field">
          <label htmlFor="target_url">Upstream MCP URL</label>
          <input id="target_url" value={form.target_url} onChange={set("target_url")} required
                 placeholder="http://rancher-mcp.apps.svc.cluster.local:8000/mcp" />
          <div className="hint" style={{ marginTop: 4 }}>
            Streamable HTTP endpoint of the server you are proxying, including its path.
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="auth_mode">Who may call it</label>
            <select id="auth_mode" value={form.auth_mode} onChange={set("auth_mode")}>
              <option value="api_key">API key</option>
              <option value="oauth">SSO (OAuth)</option>
              <option value="none">Anyone (no auth)</option>
            </select>
          </div>
          {form.auth_mode === "oauth" && (
            <div className="field">
              <label htmlFor="idp">Identity provider</label>
              <select id="idp" value={form.idp} onChange={set("idp")}>
                <option value="">Choose…</option>
                <option value="github">GitHub</option>
                <option value="google">Google</option>
                <option value="microsoft">Microsoft</option>
                <option value="oidc">Custom OIDC</option>
              </select>
            </div>
          )}
        </div>
        <div className="field">
          <label htmlFor="description">Description</label>
          <input id="description" value={form.description} onChange={set("description")}
                 placeholder="What this server is for" />
        </div>
        <button className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create gateway"}
        </button>
      </form>
    </Card>
  );
}
