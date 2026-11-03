import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Card, Details, Drawer, Endpoint, NoMatches, Notice, Skeleton,
  SAMPLE_GATEWAYS, SAMPLE_STATS, Stat, can, relative,
} from "../ui.jsx";
import { navigate } from "../App.jsx";

const AUTH_LABEL = { none: "open", api_key: "API key", oauth: "SSO" };

// One filter per column, under its own label. Endpoint is derived, not stored, so
// it is not filterable; the name box answers to a slug or an id too, because
// that is what people paste.
const FIELDS = [
  { key: "name", label: "Name" },
  null,
  { key: "auth_mode", label: "Auth" },
  null,
  { key: "enabled", label: "Status" },
  null,
  { key: "id", label: "ID" },
];

// Which column each header sorts by, and the direction its data suggests.
const SORTS = { created_at: "desc", updated_at: "desc", name: "asc", slug: "asc" };

// The key handed out when the tenant was created, if this tab still has it.
function takeNewConsole() {
  const raw = sessionStorage.getItem("omg.new_console");
  if (!raw) return null;
  sessionStorage.removeItem("omg.new_console");
  const gateway = JSON.parse(raw);
  return { ...gateway, endpoint: `${window.location.origin}/g/${gateway.slug}/mcp` };
}

export function Gateways({ me }) {
  const canEdit = can(me, "gateways:write");
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [filters, setFilters] = useState({});
  const [newConsole, setNewConsole] = useState(takeNewConsole);
  const [selected, setSelected] = useState(null);
  const [sort, setSort] = useSort("created_at", SORTS);
  const { items: gateways, next, busy, more, error: listError } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.gateways({ limit, before, ...query });
      return { items: page.gateways, next: page.next };
    },
    { ...filters, ...sort },
  );

  useEffect(() => {
    api.stats(null, 60).then(setStats, (err) => setError(err.message));
  }, []);

  const unauthenticated = (gateways ?? []).filter((g) => g.auth_mode === "none");
  const rejected =
    (stats?.by_outcome?.unauthenticated ?? 0) +
    (stats?.by_outcome?.forbidden ?? 0) +
    (stats?.by_outcome?.rate_limited ?? 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Gateways</h1>
          <div className="sub">
            Every MCP server you front, with its own auth, limits and traffic.
          </div>
        </div>
        {canEdit && (
          <div className="actions">
            <button className="primary" onClick={() => setCreating(true)}>
              New gateway
            </button>
          </div>
        )}
      </div>

      <Notice kind="err">{error || listError}</Notice>

      {newConsole && (
        <Card
          title="Your tenant's console gateway"
          actions={
            <button className="ghost sm" onClick={() => setNewConsole(null)}>
              Dismiss
            </button>
          }
        >
          <p className="sub" style={{ marginBottom: 12 }}>
            Every tenant ships with a gateway to this deployment's own read-only
            MCP — gateways, traffic, audit log and this manual. Point an agent at
            it and ask what the deployment is doing. The key is shown once.
          </p>
          <div className="field">
            <div className="hint">Endpoint</div>
            <Endpoint value={newConsole.endpoint} />
          </div>
          <div className="field">
            <div className="hint">API key</div>
            <Endpoint value={newConsole.api_key} />
          </div>
        </Card>
      )}

      {unauthenticated.length > 0 && (
        <Notice kind="info">
          {unauthenticated.length === 1 ? "1 gateway is" : `${unauthenticated.length} gateways are`} proxying with no
          authentication. Fine on a private network; require a key or SSO before exposing them.
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 12 }}>
        <Skeleton data={gateways} sample={SAMPLE_GATEWAYS} label="Loading gateways">
          {(rows) => (
            <Stat label="Gateways" value={rows.length} hint={`${rows.filter((g) => g.enabled).length} enabled`} />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading requests">
          {(data) => (
            <Stat
              label="Requests / 60 min"
              value={data.requests}
              hint={`avg ${data.avg_duration_ms} ms`}
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label="Loading rejections">
          {(data) =>
            rejected === 0 ? (
              <Stat label="Rejected" value={0} hint="401 / 403 / 429" />
            ) : (
              <Stat
                label="Rejected"
                value={
                  (data.by_outcome?.unauthenticated ?? 0) +
                  (data.by_outcome?.forbidden ?? 0) +
                  (data.by_outcome?.rate_limited ?? 0)
                }
                hint="401 / 403 / 429"
              />
            )
          }
        </Skeleton>
      </div>

      {creating && (
        <Drawer title="New gateway" onClose={() => setCreating(false)}>
          <CreateGateway
            onCancel={() => setCreating(false)}
            // Straight to the new gateway: its endpoint and key are the next
            // thing anyone needs, and the list only repeats the name.
            onCreated={(id) => { setCreating(false); navigate(`/gateways/${id}`); }}
          />
        </Drawer>
      )}

      <Card title="All gateways" aside={gateways ? `${gateways.length} shown` : null}>
        <Skeleton
          data={gateways}
          sample={SAMPLE_GATEWAYS}
          label="Loading the gateway list"
          empty={gateways?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title="No gateways yet"
              action={
                canEdit ? (
                  <button className="sm" onClick={() => setCreating(true)}>
                    Create the first gateway
                  </button>
                ) : null
              }
            >
              Create one to expose an MCP server.
            </NoMatches>
          }
        >
          {(rows) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <SortHeader field="name" label="Name" sort={sort} onSort={setSort} />
                    <th>Endpoint</th>
                    <th>Auth</th>
                    <th className="num">Limit</th>
                    <th>Status</th>
                    <SortHeader
                      field="updated_at"
                      label="Updated"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <th>ID</th>
                  </tr>
                  <FilterRow fields={FIELDS} value={filters} onChange={setFilters} />
                </thead>
                <tbody>
                  {rows.map((gateway) => (
                    <tr
                      key={gateway.id}
                      className="clickable"
                      tabIndex={0}
                      onClick={() => setSelected(gateway)}
                      onKeyDown={(e) => { if (e.key === "Enter") setSelected(gateway); }}
                    >
                      <td className="strong nowrap">
                        {gateway.name}
                        <div className="sub-line">{gateway.slug}</div>
                      </td>
                      <td className="mono">{gateway.endpoint}</td>
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
                      <td className="num hint">{relative(gateway.updated_at)}</td>
                      <td className="mono hint">{gateway.id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
        <LoadMore next={next} busy={busy} onClick={more} />
      </Card>

      {selected && (
        <Drawer
          title={selected.name}
          sub={selected.slug}
          onClose={() => setSelected(null)}
          actions={
            <button
              className="ghost sm"
              onClick={() => { setSelected(null); navigate(`/gateways/${selected.id}`); }}
            >
              Open full page
            </button>
          }
        >
          <div className="field">
            <div className="hint">Endpoint</div>
            <Endpoint value={selected.endpoint} />
          </div>
          <Details
            rows={[
              ["Name", selected.name],
              ["Slug", <span className="mono">{selected.slug}</span>],
              ["Upstream", <span className="mono">{selected.target_url}</span>],
              [
                "Auth",
                <Badge kind={selected.auth_mode === "none" ? "warn" : "info"}>
                  {AUTH_LABEL[selected.auth_mode] ?? selected.auth_mode}
                  {selected.idp ? ` · ${selected.idp}` : ""}
                </Badge>,
              ],
              ["Rate limit", selected.requests_per_minute ? `${selected.requests_per_minute}/min` : "none"],
              ["Status", <Badge kind={selected.enabled ? "ok" : "mute"}>{selected.enabled ? "enabled" : "disabled"}</Badge>],
              ["Description", selected.description || "—"],
              ["Created", new Date(selected.created_at).toLocaleString()],
              ["Updated", relative(selected.updated_at)],
              ["Gateway id", <span className="mono">{selected.id}</span>],
            ]}
          />
          <p className="hint" style={{ marginBottom: 0 }}>
            Keys, the upstream credential and the connect guide live on the gateway's own
            page.
          </p>
        </Drawer>
      )}
    </>
  );
}

function CreateGateway({ onCreated }) {
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
                 pattern="[a-z0-9-]+" placeholder="rancher" className="mono" />
          <div className="hint">
            Becomes <span className="mono">/g/{form.slug || "slug"}/mcp</span>
          </div>
        </div>
      </div>
      <div className="field">
        <label htmlFor="target_url">Upstream MCP URL</label>
        <input id="target_url" value={form.target_url} onChange={set("target_url")} required
               placeholder="http://rancher-mcp.apps.svc.cluster.local:8000/mcp" className="mono" />
        <div className="hint">
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
        {form.auth_mode === "oauth" ? (
          <div className="field">
            <label htmlFor="idp">Identity provider</label>
            <select id="idp" value={form.idp} onChange={set("idp")}>
              <option value="">Choose…</option>
              <option value="github">GitHub</option>
              <option value="google">Google</option>
              <option value="azure">Microsoft</option>
              <option value="oidc">Custom OIDC</option>
            </select>
          </div>
        ) : <div />}
      </div>
      <div className="field">
        <label htmlFor="description">Description</label>
        <input id="description" value={form.description} onChange={set("description")}
               placeholder="What this server is for" />
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create gateway"}
        </button>
      </div>
    </form>
  );
}
