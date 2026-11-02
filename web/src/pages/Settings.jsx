import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, Notice, relative } from "../ui.jsx";

// Google sign-in for the panel itself. Separate from a gateway's own OAuth:
// this one decides who may administer the installation, so it sits behind the
// administrator check and its secret never comes back from the API.
export function Settings() {
  const [members, setMembers] = useState([]);
  const [newMember, setNewMember] = useState({ email: "", password: "" });
  const [memberBanner, setMemberBanner] = useState(null);
  const [form, setForm] = useState({
    google_client_id: "",
    google_client_secret: "",
    redirect_uri: "",
    operator_allowlist: "",
  });
  const [hasSecret, setHasSecret] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.signInSettings()
      .then((data) => {
        setForm({
          google_client_id: data.google_client_id,
          google_client_secret: "",
          redirect_uri: data.redirect_uri,
          operator_allowlist: (data.operator_allowlist ?? []).join(", "),
        });
        setHasSecret(data.has_google_client_secret);
        setEnabled(data.enabled);
      })
      .catch((err) => setBanner({ kind: "err", text: err.message }));
  }, []);

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  const loadMembers = () =>
    api.operators().then((data) => setMembers(data.operators), () => setMembers([]));

  useEffect(() => { loadMembers(); }, []);

  async function addMember(event) {
    event.preventDefault();
    setMemberBanner(null);
    try {
      await api.addOperator(newMember.email, newMember.password);
      setNewMember({ email: "", password: "" });
      setMemberBanner({ kind: "ok", text: "Member added. They sign in with that password." });
      loadMembers();
    } catch (err) {
      setMemberBanner({ kind: "err", text: err.message });
    }
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setBanner(null);
    try {
      const result = await api.saveSignInSettings({
        google_client_id: form.google_client_id,
        // Blank keeps the stored secret; the panel never receives it back.
        google_client_secret: form.google_client_secret || null,
        redirect_uri: form.redirect_uri,
        operator_allowlist: form.operator_allowlist
          .split(",")
          .map((email) => email.trim())
          .filter(Boolean),
      });
      setEnabled(result.enabled);
      setHasSecret(true);
      setForm({ ...form, google_client_secret: "" });
      setBanner({ kind: "ok", text: "Saved. The sign-in page reflects it immediately." });
    } catch (err) {
      setBanner({ kind: "err", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Settings</h1>
          <p className="hint">
            Who may administer this installation. Separate from the OAuth that
            fronts each MCP gateway.
          </p>
        </div>
      </div>

      <Card title="Who belongs to this tenant">
        <Notice kind="err">{memberBanner?.kind === "err" ? memberBanner.text : ""}</Notice>
        <Notice kind="ok">{memberBanner?.kind === "ok" ? memberBanner.text : ""}</Notice>
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Registered</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.operator_id}>
                <td className="mono" style={{ fontSize: 12 }}>{member.email}</td>
                <td>{member.role}</td>
                <td className="hint">{relative(member.registered_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <form onSubmit={addMember} className="row" style={{ marginTop: 14 }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label>Email</label>
            <input
              type="email"
              value={newMember.email}
              onChange={(e) => setNewMember({ ...newMember, email: e.target.value })}
              required
            />
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <label>Initial password</label>
            <input
              type="password"
              minLength={12}
              value={newMember.password}
              onChange={(e) => setNewMember({ ...newMember, password: e.target.value })}
              required
            />
          </div>
          <button className="primary" style={{ alignSelf: "flex-end" }}>Add member</button>
        </form>
        <p className="hint" style={{ marginBottom: 0 }}>
          Members share this tenant's gateways and traffic, read-only. Registration is
          closed after the first account, so this is the only way in.
        </p>
      </Card>

      <Card title="Sign in with Google">
        <Notice kind="ok">
          {enabled ? "Enabled — the login page offers Google." : "Off."}
        </Notice>
        <Notice kind="err">{banner?.kind === "err" ? banner.text : ""}</Notice>
        <Notice kind="ok">{banner?.kind === "ok" ? banner.text : ""}</Notice>

        <form onSubmit={save} className="stack">
          <div className="field">
            <label>Authorised redirect URI</label>
            <input
              value={form.redirect_uri}
              onChange={set("redirect_uri")}
              className="mono"
              placeholder="https://your-host/api/auth/google/callback"
              required
            />
            <div className="hint" style={{ marginTop: 4 }}>
              Must match what you registered at the provider, byte for byte. A
              mismatch fails at Google, in a browser, with no mention of this page.
            </div>
          </div>
          <div className="field">
            <label>Client ID</label>
            <input
              value={form.google_client_id}
              onChange={set("google_client_id")}
              className="mono"
              placeholder="…apps.googleusercontent.com"
              required
            />
          </div>
          <div className="field">
            <label>Client secret</label>
            <input
              type="password"
              value={form.google_client_secret}
              onChange={set("google_client_secret")}
              className="mono"
              placeholder={hasSecret ? "stored — leave blank to keep it" : "required"}
            />
            <div className="hint" style={{ marginTop: 4 }}>
              Write-only. {hasSecret ? "A secret is stored." : "No secret stored yet."}
            </div>
          </div>
          <div className="field">
            <label>Who may sign in</label>
            <textarea
              rows={3}
              value={form.operator_allowlist}
              onChange={set("operator_allowlist")}
              placeholder="you@example.com, teammate@example.com"
            />
            <div className="hint" style={{ marginTop: 4 }}>
              Comma separated. Signing in creates the operator, so an empty list
              means nobody new can get in.
            </div>
          </div>
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </form>
      </Card>
    </>
  );
}
