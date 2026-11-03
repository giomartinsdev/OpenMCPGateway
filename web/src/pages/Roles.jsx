import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useT } from "../i18n.jsx";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Card, Details, Drawer, NoMatches, Notice, Skeleton, SAMPLE_ROLES, can,
} from "../ui.jsx";

// Two roles cannot be edited: `owner` and `viewer` exist in code, so a
// deployment that lost its log still has an owner. Everything else here is the
// tenant's own.
export function Roles({ me }) {
  const t = useT();
  const canWrite = can(me, "roles:write");
  const [catalog, setCatalog] = useState([]);
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("name", { name: "asc" });
  const {
    items: roles, next, busy: listBusy, more, reload,
  } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.roles({ limit, before, ...query });
      return { items: page.roles, next: page.next };
    },
    { ...filters, ...sort },
  );
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null);
  // One slot for both drawers: reading a role, and editing one.
  const [drawer, setDrawer] = useState(null); // {kind: "role", role} | {kind: "edit", editing}
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      // One row is enough to read the catalog, which does not page.
      const data = await api.roles({ limit: 1 });
      setCatalog(data.permissions);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  function startCreate() {
    setBanner(null);
    setDrawer({ kind: "edit", editing: { id: null, name: "", permissions: [] } });
  }

  function startEdit(role) {
    setBanner(null);
    setDrawer({
      kind: "edit",
      editing: { id: role.id, name: role.name, permissions: role.permissions },
    });
  }

  function toggle(permission) {
    const { editing } = drawer;
    const has = editing.permissions.includes(permission);
    setDrawer({
      ...drawer,
      editing: {
        ...editing,
        permissions: has
          ? editing.permissions.filter((p) => p !== permission)
          : [...editing.permissions, permission],
      },
    });
  }

  async function save(event) {
    event.preventDefault();
    const { editing } = drawer;
    setBusy(true);
    setBanner(null);
    try {
      if (editing.id) await api.updateRole(editing.id, editing.name, editing.permissions);
      else await api.defineRole(editing.name, editing.permissions);
      setDrawer(null);
      setBanner({
        kind: "ok",
        text: t("Saved. Members holding this role see it on their next click."),
      });
      reload();
    } catch (err) {
      setBanner({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function remove(role) {
    setBanner(null);
    try {
      await api.deleteRole(role.id);
      setDrawer(null);
      setBanner({ kind: "ok", text: t('Deleted "{name}".', { name: role.name }) });
      reload();
    } catch (err) {
      setBanner({ kind: "err", text: err.message });
    }
  }

  if (error) return <Notice kind="err">{error}</Notice>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t("Roles")}</h1>
          <div className="sub">
            {t("What a member may do. Permissions are checked on every request, so a change lands on their next click — no re-login, no stale cookie.")}
          </div>
        </div>
        {canWrite && (
          <div className="actions">
            <button className="primary" onClick={startCreate}>{t("New role")}</button>
          </div>
        )}
      </div>

      <Notice kind="err">{banner?.kind === "err" ? banner.text : ""}</Notice>
      <Notice kind="ok">{banner?.kind === "ok" ? banner.text : ""}</Notice>

      <Card
        title={t("All roles")}
        aside={roles ? t("{count} roles", { count: roles.length }) : null}
      >
        <Skeleton
          data={roles}
          sample={SAMPLE_ROLES}
          label={t("Loading roles")}
          empty={roles?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title={t("No roles")}
              action={
                canWrite ? (
                  <button className="sm" onClick={startCreate}>
                    {t("Define the first role")}
                  </button>
                ) : null
              }
            >
              {t("Define one to grant a narrow set of permissions.")}
            </NoMatches>
          }
        >
          {(rows) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <SortHeader
                      field="name"
                      label="Role"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>{t("Permissions")}</th>
                    <th className="num">{t("Holders")}</th>
                    <th>{t("ID")}</th>
                    <th />
                  </tr>
                  <FilterRow
                    fields={[
                      { key: "name", label: "Role" },
                      null,
                      null,
                      { key: "id", label: "ID" },
                      null,
                    ]}
                    value={filters}
                    onChange={setFilters}
                  />
                </thead>
                <tbody>
                  {rows.map((role) => (
                    <tr
                      key={role.id}
                      className="clickable"
                      tabIndex={0}
                      onClick={() => setDrawer({ kind: "role", role })}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") setDrawer({ kind: "role", role });
                      }}
                    >
                      <td className="strong nowrap">{role.name}</td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {role.permissions.length === 0 ? (
                            <span className="hint">{t("none — can read nothing")}</span>
                          ) : (
                            role.permissions.map((permission) => (
                              <span key={permission} className="badge mute mono">
                                {permission}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td className="num">
                        {role.builtin ? <Badge kind="purple">{t("built-in")}</Badge> : role.holders}
                      </td>
                      <td className="mono hint">{role.id}</td>
                      <td className="actions">
                        {canWrite && !role.builtin && (
                          <>
                            <button
                              className="ghost sm"
                              onClick={(event) => {
                                event.stopPropagation();
                                startEdit(role);
                              }}
                            >
                              {t("Edit")}
                            </button>
                            <button
                              className="ghost sm danger"
                              onClick={(event) => {
                                event.stopPropagation();
                                remove(role);
                              }}
                            >
                              {t("Delete")}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
        <LoadMore next={next} busy={listBusy} onClick={more} />
        <p className="hint" style={{ marginBottom: 0, marginTop: 12 }}>
          {t("A permission outside the catalog is refused when you save: a typo would otherwise be a role that grants nothing and says nothing.")}
        </p>
      </Card>

      {drawer?.kind === "role" && (
        <Drawer
          title={drawer.role.name}
          sub={drawer.role.id}
          onClose={() => setDrawer(null)}
          actions={
            canWrite && !drawer.role.builtin ? (
              <button className="ghost sm" onClick={() => startEdit(drawer.role)}>
                {t("Edit")}
              </button>
            ) : null
          }
        >
          <Details
            rows={[
              [t("Role"), drawer.role.name],
              [
                t("Holders"),
                drawer.role.builtin
                  ? t("built-in, held by anyone it is assigned to")
                  : drawer.role.holders,
              ],
              [
                t("Kind"),
                drawer.role.builtin ? (
                  <Badge kind="purple">{t("built-in")}</Badge>
                ) : (
                  t("this tenant's own")
                ),
              ],
              [t("Permissions"), drawer.role.permissions.length || t("none")],
            ]}
          />
          <div className="eyebrow" style={{ marginBottom: 8 }}>{t("What it grants")}</div>
          {drawer.role.permissions.length === 0 ? (
            <p className="hint">
              {t("Nothing. A role with no permissions can read nothing.")}
            </p>
          ) : (
            <div className="permissions">
              {drawer.role.permissions.map((permission) => (
                <span key={permission} className="badge mute mono">{permission}</span>
              ))}
            </div>
          )}
          <p className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
            {drawer.role.builtin
              ? t("Built-in roles are defined in code, so a deployment that lost its log still has an owner.")
              : t("Editing this role changes what every holder may do on their next request.")}
          </p>
        </Drawer>
      )}

      {drawer?.kind === "edit" && (
        <Drawer
          title={
            drawer.editing.id
              ? t('Edit "{name}"', { name: drawer.editing.name })
              : t("New role")
          }
          sub={drawer.editing.id}
          onClose={() => setDrawer(null)}
        >
          <form onSubmit={save}>
            <div className="field">
              <label htmlFor="role-name">{t("Name")}</label>
              <input
                id="role-name"
                value={drawer.editing.name}
                onChange={(e) =>
                  setDrawer({
                    ...drawer,
                    editing: { ...drawer.editing, name: e.target.value },
                  })
                }
                placeholder="Auditor"
                required
              />
            </div>
            <label>{t("Permissions")}</label>
            <div className="permissions">
              {catalog.map((permission) => (
                <label key={permission} className="permission">
                  <input
                    type="checkbox"
                    checked={drawer.editing.permissions.includes(permission)}
                    onChange={() => toggle(permission)}
                  />
                  <span className="mono">{permission}</span>
                </label>
              ))}
            </div>
            <div className="form-actions">
              <button className="primary" disabled={busy}>
                {busy ? t("Saving…") : t("Save role")}
              </button>
            </div>
          </form>
        </Drawer>
      )}
    </>
  );
}

