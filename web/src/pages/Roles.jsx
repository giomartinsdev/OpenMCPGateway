import { useEffect, useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, usePagedList } from "../paged.jsx";
import {
  Badge, Card, Empty, Notice, Skeleton, SAMPLE_ROLES, can,
} from "../ui.jsx";

// Two roles cannot be edited: `owner` and `viewer` exist in code, so a
// deployment that lost its log still has an owner. Everything else here is the
// tenant's own.
export function Roles({ me }) {
  const canWrite = can(me, "roles:write");
  const [catalog, setCatalog] = useState([]);
  const [filters, setFilters] = useState({});
  const {
    items: roles, next, busy: listBusy, more, reload,
  } = usePagedList(
    async ({ limit, before, ...fields }) => {
      const page = await api.roles({ limit, before, ...fields });
      return { items: page.roles, next: page.next };
    },
    filters,
  );
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null);
  const [editing, setEditing] = useState(null); // {id, name, permissions}
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
    setEditing({ id: null, name: "", permissions: [] });
  }

  function startEdit(role) {
    setBanner(null);
    setEditing({ id: role.id, name: role.name, permissions: role.permissions });
  }

  function toggle(permission) {
    const has = editing.permissions.includes(permission);
    setEditing({
      ...editing,
      permissions: has
        ? editing.permissions.filter((p) => p !== permission)
        : [...editing.permissions, permission],
    });
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setBanner(null);
    try {
      if (editing.id) await api.updateRole(editing.id, editing.name, editing.permissions);
      else await api.defineRole(editing.name, editing.permissions);
      setEditing(null);
      setBanner({ kind: "ok", text: "Saved. Members holding this role see it on their next click." });
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
      setBanner({ kind: "ok", text: `Deleted "${role.name}".` });
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
          <h1>Roles</h1>
          <div className="sub">
            What a member may do. Permissions are checked on every request, so a
            change lands on their next click — no re-login, no stale cookie.
          </div>
        </div>
        {canWrite && !editing && (
          <div className="actions">
            <button className="primary" onClick={startCreate}>New role</button>
          </div>
        )}
      </div>

      <Notice kind="err">{banner?.kind === "err" ? banner.text : ""}</Notice>
      <Notice kind="ok">{banner?.kind === "ok" ? banner.text : ""}</Notice>

      {editing && (
        <Card title={editing.id ? `Edit "${editing.name}"` : "New role"}>
          <form onSubmit={save}>
            <div className="field" style={{ maxWidth: 280 }}>
              <label htmlFor="role-name">Name</label>
              <input
                id="role-name"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="Auditor"
                required
              />
            </div>
            <label>Permissions</label>
            <div className="permissions">
              {catalog.map((permission) => (
                <label key={permission} className="permission">
                  <input
                    type="checkbox"
                    checked={editing.permissions.includes(permission)}
                    onChange={() => toggle(permission)}
                  />
                  <span className="mono">{permission}</span>
                </label>
              ))}
            </div>
            <div className="form-actions">
              <button className="primary" disabled={busy}>
                {busy ? "Saving…" : "Save role"}
              </button>
              <button type="button" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}

      <Card title="All roles" aside={roles ? `${roles.length} roles` : null}>
        <Skeleton
          data={roles}
          sample={SAMPLE_ROLES}
          label="Loading roles"
          empty={roles?.length === 0}
          emptyMessage={<Empty title="No roles">Define one to grant a narrow set of permissions.</Empty>}
        >
          {(rows) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Role</th>
                    <th>Permissions</th>
                    <th className="num">Holders</th>
                    <th />
                  </tr>
                  <FilterRow
                    fields={[{ key: "name", label: "Role" }, null, null, null]}
                    value={filters}
                    onChange={setFilters}
                  />
                </thead>
                <tbody>
                  {rows.map((role) => (
                    <tr key={role.id}>
                      <td className="strong nowrap">
                        {role.name}
                        <div className="sub-line">{role.id}</div>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {role.permissions.length === 0 ? (
                            <span className="hint">none — can read nothing</span>
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
                        {role.builtin ? <Badge kind="purple">built-in</Badge> : role.holders}
                      </td>
                      <td className="actions">
                        {canWrite && !role.builtin && (
                          <>
                            <button className="ghost sm" onClick={() => startEdit(role)}>
                              Edit
                            </button>
                            <button className="ghost sm danger" onClick={() => remove(role)}>
                              Delete
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
          A permission outside the catalog is refused when you save: a typo would
          otherwise be a role that grants nothing and says nothing.
        </p>
      </Card>
    </>
  );
}
