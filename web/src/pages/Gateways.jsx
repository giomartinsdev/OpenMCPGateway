import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import { useT } from "../i18n.jsx";
import {
  AuthBadge, Badge, Card, Drawer, Endpoint, NoMatches, Notice, Skeleton,
  SAMPLE_GATEWAYS, SAMPLE_STATS, Stat, can, relative,
} from "../ui.jsx";
import { Flow } from "../Sankey.jsx";
import { navigate } from "../App.jsx";

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

// The flow's window. Same four the traffic page offers, so "1 hour" means one
// thing in the panel.
const WINDOWS = [
  [60, "1 hour"],
  [360, "6 hours"],
  [1440, "24 hours"],
  [10080, "7 days"],
];

// The key handed out when the tenant was created, if this tab still has it.
function takeNewConsole() {
  const raw = sessionStorage.getItem("omg.new_console");
  if (!raw) return null;
  sessionStorage.removeItem("omg.new_console");
  const gateway = JSON.parse(raw);
  return { ...gateway, endpoint: `${window.location.origin}/g/${gateway.slug}/mcp` };
}

export function Gateways({ me }) {
  const t = useT();
  const canEdit = can(me, "gateways:write");
  const [stats, setStats] = useState(null);
  const [minutes, setMinutes] = useState(60);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [filters, setFilters] = useState({});
  const [newConsole, setNewConsole] = useState(takeNewConsole);
  const [sort, setSort] = useSort("created_at", SORTS);
  const { items: gateways, next, busy, more, error: listError } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.gateways({ limit, before, ...query });
      return { items: page.gateways, next: page.next };
    },
    { ...filters, ...sort },
  );

  useEffect(() => {
    api.stats(null, minutes).then(setStats, (err) => setError(err.message));
  }, [minutes]);

  const unauthenticated = (gateways ?? []).filter((g) => g.auth_mode === "none");
  const rejected =
    (stats?.by_outcome?.unauthenticated ?? 0) +
    (stats?.by_outcome?.forbidden ?? 0) +
    (stats?.by_outcome?.rate_limited ?? 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t("Gateways")}</h1>
          <div className="sub">
            {t("Every MCP server you front, with its own auth, limits and traffic.")}
          </div>
        </div>
        {canEdit && (
          <div className="actions">
            <button className="primary" onClick={() => setCreating(true)}>
              {t("New gateway")}
            </button>
          </div>
        )}
      </div>

      <Notice kind="err">{error || listError}</Notice>

      {newConsole && (
        <Card
          title={t("Your tenant's console gateway")}
          actions={
            <button className="ghost sm" onClick={() => setNewConsole(null)}>
              {t("Dismiss")}
            </button>
          }
        >
          <p className="sub" style={{ marginBottom: 12 }}>
            {t("Every tenant ships with a gateway to this deployment's own read-only MCP — gateways, traffic, audit log and this manual. Point an agent at it and ask what the deployment is doing. The key is shown once.")}
          </p>
          <div className="field">
            <div className="hint">{t("Endpoint")}</div>
            <Endpoint value={newConsole.endpoint} />
          </div>
          <div className="field">
            <div className="hint">{t("API key")}</div>
            <Endpoint value={newConsole.api_key} />
          </div>
        </Card>
      )}

      {unauthenticated.length > 0 && (
        <Notice kind="info">
          {unauthenticated.length === 1
            ? t("1 gateway is proxying with no authentication. Fine on a private network; require a key or SSO before exposing it.")
            : t("{count} gateways are proxying with no authentication. Fine on a private network; require a key or SSO before exposing them.", { count: unauthenticated.length })}
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 12 }}>
        <Skeleton data={gateways} sample={SAMPLE_GATEWAYS} label={t("Loading gateways")}>
          {(rows) => (
            <Stat
              label={t("Gateways")}
              value={rows.length}
              hint={t("{count} enabled", { count: rows.filter((g) => g.enabled).length })}
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading requests")}>
          {(data) => (
            <Stat
              label={t("Requests / 60 min")}
              value={data.requests}
              hint={t("avg {ms} ms", { ms: data.avg_duration_ms })}
            />
          )}
        </Skeleton>
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading rejections")}>
          {(data) =>
            rejected === 0 ? (
              <Stat label={t("Rejected")} value={0} hint="401 / 403 / 429" />
            ) : (
              <Stat
                label={t("Rejected")}
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

      {/* The flow, not another row of numbers: which gateway the traffic enters
          through and which outcome it leaves by, in one picture. The window is
          the reader's choice because "now" is the wrong answer when a gateway
          stopped answering an hour ago. */}
      <Card
        title={t("Request flow")}
        aside={t("Where requests came in, and how they left.")}
        actions={
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
        }
      >
        <Skeleton data={stats} sample={SAMPLE_STATS} label={t("Loading the flow")}>
          {(data) => <Flow stats={data} />}
        </Skeleton>
      </Card>

      {creating && (
        <Drawer title={t("New gateway")} onClose={() => setCreating(false)}>
          <CreateGateway
            onCancel={() => setCreating(false)}
            // Straight to the new gateway: its endpoint and key are the next
            // thing anyone needs, and the list only repeats the name.
            onCreated={(id) => { setCreating(false); navigate(`/gateways/${id}`); }}
          />
        </Drawer>
      )}

      <Card
        title={t("All gateways")}
        aside={gateways ? t("{count} shown", { count: gateways.length }) : null}
      >
        <Skeleton
          data={gateways}
          sample={SAMPLE_GATEWAYS}
          label={t("Loading the gateway list")}
          empty={gateways?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title={t("No gateways yet")}
              action={
                canEdit ? (
                  <button className="sm" onClick={() => setCreating(true)}>
                    {t("Create the first gateway")}
                  </button>
                ) : null
              }
            >
              {t("Create one to expose an MCP server.")}
            </NoMatches>
          }
        >
          {(rows) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <SortHeader field="name" label="Name" sort={sort} onSort={setSort} />
                    <th>{t("Endpoint")}</th>
                    <th>{t("Auth")}</th>
                    <th className="num">{t("Limit")}</th>
                    <th>{t("Status")}</th>
                    <SortHeader
                      field="updated_at"
                      label="Updated"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <th>{t("ID")}</th>
                  </tr>
                  <FilterRow fields={FIELDS} value={filters} onChange={setFilters} />
                </thead>
                <tbody>
                  {rows.map((gateway) => (
                    <tr
                      key={gateway.id}
                      className="clickable"
                      tabIndex={0}
                      onClick={() => navigate(`/gateways/${gateway.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") navigate(`/gateways/${gateway.id}`);
                      }}
                    >
                      <td className="strong nowrap">
                        {gateway.name}
                        <div className="sub-line">{gateway.slug}</div>
                      </td>
                      <td className="mono">{gateway.endpoint}</td>
                      <td>
                        <AuthBadge gateway={gateway} t={t} />
                      </td>
                      <td className="num">
                        {gateway.requests_per_minute
                          ? `${gateway.requests_per_minute}/min`
                          : "—"}
                      </td>
                      <td>
                        <Badge kind={gateway.enabled ? "ok" : "mute"}>
                          {gateway.enabled ? t("enabled") : t("disabled")}
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

    </>
  );
}

function CreateGateway({ onCreated }) {
  const t = useT();
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
          <label htmlFor="name">{t("Name")}</label>
          <input id="name" value={form.name} onChange={set("name")} required
                 placeholder={t("Rancher cluster")} />
        </div>
        <div className="field">
          <label htmlFor="slug">{t("Slug")}</label>
          <input id="slug" value={form.slug} onChange={set("slug")} required
                 // The hyphen is escaped because the `pattern` attribute is
                 // compiled with the `v` flag, where a bare `-` inside a class is
                 // an invalid character class — the whole pattern is then ignored
                 // and the field silently stops validating.
                 pattern="[a-z0-9\-]+" placeholder="rancher" className="mono" />
          <div className="hint">
            {t("Becomes")} <span className="mono">/g/{form.slug || t("slug")}/mcp</span>
          </div>
        </div>
      </div>
      <div className="field">
        <label htmlFor="target_url">{t("Upstream MCP URL")}</label>
        <input id="target_url" value={form.target_url} onChange={set("target_url")} required
               placeholder="http://rancher-mcp.apps.svc.cluster.local:8000/mcp" className="mono" />
        <div className="hint">
          {t("Streamable HTTP endpoint of the server you are proxying, including its path.")}
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="auth_mode">{t("Who may call it")}</label>
          <select id="auth_mode" value={form.auth_mode} onChange={set("auth_mode")}>
            <option value="api_key">{t("API key")}</option>
            <option value="oauth">{t("SSO (OAuth)")}</option>
            <option value="none">{t("Anyone (no auth)")}</option>
          </select>
        </div>
        {form.auth_mode === "oauth" ? (
          <div className="field">
            <label htmlFor="idp">{t("Identity provider")}</label>
            <select id="idp" value={form.idp} onChange={set("idp")}>
              <option value="">{t("Choose…")}</option>
              <option value="self">{t("This deployment (no setup)")}</option>
              <option value="github">GitHub</option>
              <option value="google">Google</option>
              <option value="azure">Microsoft</option>
              <option value="oidc">{t("Custom OIDC")}</option>
            </select>
          </div>
        ) : <div />}
      </div>
      <div className="field">
        <label htmlFor="description">{t("Description")}</label>
        <input id="description" value={form.description} onChange={set("description")}
               placeholder={t("What this server is for")} />
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {busy ? t("Creating…") : t("Create gateway")}
        </button>
      </div>
    </form>
  );
}
