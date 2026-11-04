import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useI18n, useT } from "../i18n.jsx";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Bone, Card, Details, Drawer, Endpoint, NoMatches, Notice, Skeleton,
  SAMPLE_MEMBERS, can, relative,
} from "../ui.jsx";

// People, not settings. Sign-in configuration is a property of the deployment, so
// this screen is only who belongs to the tenant and what each of them may do.
export function Members({ me }) {
  const t = useT();
  const { locale } = useI18n();
  const canWrite = can(me, "members:write");
  const canAssign = can(me, "roles:write");
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("email", { email: "asc", created_at: "desc" });
  const [roles, setRoles] = useState([]);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null);
  const [form, setForm] = useState({ email: "", password: "", roleIds: [] });
  // Which drawer is open: the new-member form, one member's details, or the
  // roles of one member. One slot, because two drawers stacked is a maze.
  const [drawer, setDrawer] = useState(null); // {kind: "new"} | {kind: "member", operator} | {kind: "roles", operator}
  const [busy, setBusy] = useState(false);
  const [tenant, setTenant] = useState(null);

  const {
    items: operators, next, busy: listBusy, more, reload, error: listError,
    goTo, page, pages,
  } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.operators({ limit, before, ...query });
      return { items: page.operators, next: page.next };
    },
    { ...filters, ...sort },
  );

  async function load() {
    try {
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
      setDrawer(null);
      setBanner({
        kind: "ok",
        text: t("Member added. They sign in with that password and the role you chose."),
      });
      reload();
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
      await api.assignRoles(drawer.operator.operator_id, drawer.roleIds);
      setDrawer(null);
      setBanner({
        kind: "ok",
        text: t("Roles changed. The member's next request already uses them."),
      });
      reload();
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
      <div className="page-head">
        <div>
          <h1>{t("Members")}</h1>
          <div className="sub">
            {t("Everyone in this tenant shares its gateways and traffic. What each of them may change comes from their roles.")}
          </div>
        </div>
        <div className="actions">
          <Badge kind="info">
            {operators ? t("{count} people", { count: operators.length }) : t("loading")}
          </Badge>
          {canWrite && (
            <button
              className="primary"
              onClick={() => { setBanner(null); setDrawer({ kind: "new" }); }}
            >
              {t("Add member")}
            </button>
          )}
        </div>
      </div>

      <Notice kind="err">{banner?.kind === "err" ? banner.text : listError}</Notice>
      <Notice kind="ok">{banner?.kind === "ok" ? banner.text : ""}</Notice>

      <Card title={t("Join code")}>
        <p className="hint" style={{ marginTop: 0, marginBottom: 8 }}>
          {t("Anyone with this code can join {tenant} as a viewer. Share it with the people who should see these gateways.", {
            tenant: tenant?.name ?? t("this tenant"),
          })}
        </p>
        {tenant ? <Endpoint value={tenant.join_code} /> : <Bone height={38} radius={6} />}
      </Card>

      <Card
        title={t("Who belongs to this tenant")}
        aside={operators ? t("{count} people", { count: operators.length }) : null}
      >
        <Skeleton
          data={operators}
          sample={SAMPLE_MEMBERS}
          label={t("Loading members")}
          empty={operators?.length === 0}
          emptyMessage={
            <NoMatches
              filters={filters}
              onClear={() => setFilters({})}
              title={t("No accounts yet")}
              action={
                canWrite ? (
                  <button className="sm" onClick={() => setDrawer({ kind: "new" })}>
                    {t("Add the first member")}
                  </button>
                ) : null
              }
            >
              {t("Add the first member.")}
            </NoMatches>
          }
        >
          {(rows) => (
            <div className="table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <SortHeader
                      field="email"
                      label="Email"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>{t("Roles")}</th>
                    <SortHeader
                      field="created_at"
                      label="Joined"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <th>{t("ID")}</th>
                    <th><span className="sr-only">{t("Operations")}</span></th>
                  </tr>
                  <FilterRow
                    fields={[
                      { key: "email", label: "Email" },
                      null,
                      { key: "created_at", label: "Joined" },
                      { key: "id", label: "ID" },
                      null,
                    ]}
                    value={filters}
                    onChange={setFilters}
                  />
                </thead>
                <tbody>
                  {rows.map((operator) => (
                    <tr
                      key={operator.operator_id}
                      className="clickable"
                      tabIndex={0}
                      onClick={() => setDrawer({ kind: "member", operator })}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") setDrawer({ kind: "member", operator });
                      }}
                    >
                      <td className="mono strong">
                        {operator.email}
                        {operator.disabled && (
                          <Badge kind="err">{t("disabled")}</Badge>
                        )}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {operator.roles.map((role) => (
                            <Badge key={role} kind="mute">{role}</Badge>
                          ))}
                        </div>
                      </td>
                      <td className="num hint">{relative(operator.created_at)}</td>
                      <td className="mono hint">{operator.operator_id}</td>
                      <td className="actions">
                        {canAssign && (
                          <button
                            className="ghost sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              setBanner(null);
                              setDrawer({
                                kind: "roles",
                                operator,
                                roleIds: operator.role_ids,
                              });
                            }}
                          >
                            {t("Change roles")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
        <LoadMore next={next} busy={listBusy} onClick={more} shown={operators?.length} pages={pages} page={page} goTo={goTo} />
      </Card>

      {drawer?.kind === "new" && (
        <Drawer title={t("Add a member")} onClose={() => setDrawer(null)}>
          <form onSubmit={add}>
            <div className="field-row">
              <div className="field">
                <label htmlFor="member-email">{t("Email")}</label>
                <input
                  id="member-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="member-password">{t("Initial password")}</label>
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
            <label>{t("Roles")}</label>
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
                  <span className="hint mono">
                    {t("{count} perms", { count: role.permissions.length })}
                  </span>
                </label>
              ))}
            </div>
            <div className="form-actions">
              <button className="primary" disabled={busy}>
                {busy ? t("Adding…") : t("Save member")}
              </button>
            </div>
            <p className="hint" style={{ marginBottom: 0 }}>
              {t("No role selected means {role}: reads the gateways and traffic, changes nothing. A colleague can also join by themselves with the tenant's join code.", {
                role: defaultRole?.name ?? "viewer",
              })}
            </p>
          </form>
        </Drawer>
      )}

      {drawer?.kind === "member" && (
        <Drawer
          title={drawer.operator.email}
          sub={drawer.operator.operator_id}
          onClose={() => setDrawer(null)}
          actions={
            canWrite ? (
              <>
                {canAssign && (
                  <button
                    className="ghost sm"
                    onClick={() =>
                      setDrawer({
                        kind: "roles",
                        operator: drawer.operator,
                        roleIds: drawer.operator.role_ids,
                      })
                    }
                  >
                    {t("Change roles")}
                  </button>
                )}
                {/* Offboarding, not deletion: the events this account wrote are
                    the record of what it did with access, and rewriting history
                    to tidy up a departure is the wrong trade. */}
                {drawer.operator.operator_id !== me.operator_id && (
                  <button
                    className={drawer.operator.disabled ? "ghost sm" : "ghost sm danger"}
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setBanner(null);
                      try {
                        if (drawer.operator.disabled) {
                          await api.enableOperator(drawer.operator.operator_id);
                        } else {
                          await api.disableOperator(drawer.operator.operator_id);
                        }
                        setDrawer(null);
                        setBanner({
                          kind: "ok",
                          text: drawer.operator.disabled
                            ? t("Account enabled. Their password still works.")
                            : t("Account disabled. Live sessions stop on the next request."),
                        });
                        reload();
                      } catch (err) {
                        setBanner({ kind: "err", text: err.message });
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {drawer.operator.disabled ? t("Enable") : t("Disable")}
                  </button>
                )}
              </>
            ) : null
          }
        >
          <Details
            rows={[
              [t("Email"), <span className="mono">{drawer.operator.email}</span>],
              [
                t("Status"),
                drawer.operator.disabled ? (
                  <Badge kind="err">{t("disabled")}</Badge>
                ) : (
                  <Badge kind="ok">{t("active")}</Badge>
                ),
              ],
              [t("Joined"), new Date(drawer.operator.created_at).toLocaleString(locale)],
              [t("Seen"), relative(drawer.operator.created_at)],
              [
                t("Roles"),
                <span className="row" style={{ gap: 4 }}>
                  {drawer.operator.roles.length === 0
                    ? t("none")
                    : drawer.operator.roles.map((role) => (
                        <Badge key={role} kind="mute">{role}</Badge>
                      ))}
                </span>,
              ],
              [t("Operator id"), <span className="mono">{drawer.operator.operator_id}</span>],
            ]}
          />
          <p className="hint" style={{ marginBottom: 0 }}>
            {t("Permissions are read from this member's roles on every request, so a change lands on their next click — no re-login.")}
          </p>
        </Drawer>
      )}

      {drawer?.kind === "roles" && (
        <Drawer
          title={t("Roles for {email}", { email: drawer.operator.email })}
          sub={drawer.operator.operator_id}
          onClose={() => setDrawer(null)}
        >
          <form onSubmit={saveRoles}>
            <div className="permissions">
              {roles.map((role) => (
                <label key={role.id} className="permission">
                  <input
                    type="checkbox"
                    checked={drawer.roleIds.includes(role.id)}
                    onChange={() =>
                      setDrawer({ ...drawer, roleIds: toggle(drawer.roleIds, role.id) })
                    }
                  />
                  <span>{role.name}</span>
                  <span className="hint mono">
                    {t("{count} perms", { count: role.permissions.length })}
                  </span>
                </label>
              ))}
            </div>
            <div className="form-actions">
              <button className="primary" disabled={busy}>
                {busy ? t("Saving…") : t("Save roles")}
              </button>
            </div>
          </form>
        </Drawer>
      )}
    </>
  );
}
