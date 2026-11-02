import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Empty, Endpoint, Notice, can, relative } from "../ui.jsx";

// People, not settings. The sign-in configuration lives in Settings; this is
// who belongs to the tenant and what each of them may do.
export function Members({ me }) {
  const canWrite = can(me, "members:write");
  const canAssign = can(me, "roles:write");
  const [operators, setOperators] = useState([]);
  const [roles, setRoles] = useState([]);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null);
  const [form, setForm] = useState({ email: "", password: "", roleIds: [] });
  const [editing, setEditing] = useState(null); // {operator, roleIds}
  const [busy, setBusy] = useState(false);
  const [tenant, setTenant] = useState(null);

  async function load() {
    try {
      const { operators } = await api.operators();
      setOperators(operators);
      // Listing members does not imply listing roles: a recruiter may add
      // people without seeing the permission table.
      if (can(me, "roles:read")) setRoles((await api.roles()).roles);
      // The join code is the invitation that costs nothing to send: no mail
      // server, no per-person link, just a string the owner shares.
      setTenant(await api.tenant());
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function add(event) {
    event.preventDefault();
    setBusy(true);
    setBanner(null);
    try {
      await api.addOperator(form.email, form.password, form.roleIds);
      setForm({ email: "", password: "", roleIds: [] });
      setBanner({
        kind: "ok",
        text: "Member added. They sign in with that password and the role you chose.",
      });
      load();
    } catch (err) {
      setBanner({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function saveRoles(event) {
    event.preventDefault();
    setBusy(true);
    setBanner(null);
    try {
      await api.assignRoles(editing.operator.operator_id, editing.roleIds);
      setEditing(null);
      setBanner({
        kind: "ok",
        text: "Roles changed. The member's next request already uses them.",
      });
      load();
    } catch (err) {
      setBanner({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  function toggle(list, id) {
    return list.includes(id) ? list.filter((r) => r !== id) : [...list, id];
  }

  if (error) return <Notice kind="err">{error}</Notice>;

  const defaultRole = roles.find((role) => role.builtin && role.name === "viewer");

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Members</h1>
          <div className="sub">
            Everyone in this tenant shares its gateways and traffic. What each of
            them may change comes from their roles.
          </div>
        </div>
      </div>

      <Notice kind="err">{banner?.kind === "err" ? banner.text : ""}</Notice>
      <Notice kind="ok">{banner?.kind === "ok" ? banner.text : ""}</Notice>

      {tenant && (
        <Card title="Join code">
          <p className="hint" style={{ marginTop: 0 }}>
            Anyone with this code can join {tenant.name} as a viewer. Share it with
            the people who should see this tenant's gateways.
          </p>
          <Endpoint value={tenant.join_code} />
        </Card>
      )}

      <Card title="Who belongs to this tenant">
        {operators.length === 0 ? (
          <Empty>No accounts yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Roles</th>
                <th>Joined</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {operators.map((operator) => (
                <tr key={operator.operator_id}>
                  <td className="mono" style={{ fontSize: 12 }}>{operator.email}</td>
                  <td>
                    <div className="row" style={{ gap: 5 }}>
                      {operator.roles.map((role) => (
                        <Badge key={role}>{role}</Badge>
                      ))}
                    </div>
                  </td>
                  <td className="hint">{relative(operator.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {canAssign && (
                      <button
                        className="ghost sm"
                        onClick={() => {
                          setBanner(null);
                          setEditing({ operator, roleIds: operator.role_ids });
                        }}
                      >
                        Change roles
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {editing && (
        <Card title={`Roles for ${editing.operator.email}`}>
          <form onSubmit={saveRoles}>
            <div className="permissions">
              {roles.map((role) => (
                <label key={role.id} className="permission">
                  <input
                    type="checkbox"
                    checked={editing.roleIds.includes(role.id)}
                    onChange={() =>
                      setEditing({ ...editing, roleIds: toggle(editing.roleIds, role.id) })
                    }
                  />
                  <span>{role.name}</span>
                  <span className="hint mono">{role.permissions.length} perms</span>
                </label>
              ))}
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button className="primary" disabled={busy}>
                {busy ? "Saving…" : "Save roles"}
              </button>
              <button type="button" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}

      {canWrite && (
        <Card title="Add a member">
          <form onSubmit={add}>
            <div className="field-row">
              <div className="field">
                <label htmlFor="member-email">Email</label>
                <input
                  id="member-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="member-password">Initial password</label>
                <input
                  id="member-password"
                  type="password"
                  minLength={12}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  required
                />
              </div>
            </div>
            <label>Roles</label>
            <div className="permissions">
              {roles.map((role) => (
                <label key={role.id} className="permission">
                  <input
                    type="checkbox"
                    checked={form.roleIds.includes(role.id)}
                    onChange={() =>
                      setForm({ ...form, roleIds: toggle(form.roleIds, role.id) })
                    }
                  />
                  <span>{role.name}</span>
                  <span className="hint mono">{role.permissions.length} perms</span>
                </label>
              ))}
            </div>
            <button className="primary" disabled={busy} style={{ marginTop: 14 }}>
              {busy ? "Adding…" : "Add member"}
            </button>
            <p className="hint" style={{ marginBottom: 0, marginTop: 10 }}>
              No role selected means {defaultRole?.name ?? "viewer"}: reads the
              gateways and traffic, changes nothing. A colleague can also join by
              themselves, with the tenant's join code.
            </p>
          </form>
        </Card>
      )}
    </>
  );
}
