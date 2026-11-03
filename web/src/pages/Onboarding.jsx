import { useState } from "react";
import { api } from "../api.js";
import { useT } from "../i18n.jsx";
import { Mark } from "../Mark.jsx";
import { Notice } from "../ui.jsx";

// What an account sees before it belongs anywhere. Signing up grants an
// identity and nothing else, so this screen is the whole product until the
// choice below is made: join a tenant with the code someone shared, or start
// one and own it.
export function Onboarding({ me, onReady }) {
  const t = useT();
  const [mode, setMode] = useState("create");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "create") {
        const created = await api.createTenant(name);
        // The key for the gateway every tenant ships with is shown once, so the
        // page it lands on has to know about it. Session storage survives the
        // route change and disappears with the tab — which is the lifetime of a
        // credential you have not copied yet.
        const gateway = created.console_gateway;
        if (gateway?.api_key) {
          sessionStorage.setItem("omg.new_console", JSON.stringify(gateway));
        }
      } else {
        await api.joinTenant(code);
      }
      // Read the identity back: the roles that came with the tenant are
      // resolved server-side, and the panel must not guess them.
      onReady(await api.me());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth">
        <div className="logo">
          <Mark size={28} />
          <div className="logo-text">
            OpenMCP<span>Gateway</span>
          </div>
        </div>
        <form className="card auth-card" onSubmit={submit}>
          <h2 style={{ marginBottom: 4 }}>
            {mode === "create" ? t("Create a tenant") : t("Join a tenant")}
          </h2>
          <p className="hint" style={{ marginBottom: 16 }}>
            {t("Signed in as {email}.", { email: me.email })}{" "}
            {mode === "create"
              ? t("A tenant is where your gateways and members live. You will own it.")
              : t("Paste the join code the tenant's owner shared with you.")}
          </p>

          <Notice kind="err">{error}</Notice>

          {mode === "create" ? (
            <div className="field">
              <label htmlFor="tenant-name">{t("Tenant name")}</label>
              <input
                id="tenant-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Acme"
                maxLength={64}
                required
              />
            </div>
          ) : (
            <div className="field">
              <label htmlFor="join-code">{t("Join code")}</label>
              <input
                id="join-code"
                value={code}
                onChange={(event) => setCode(event.target.value.toUpperCase())}
                className="mono"
                placeholder="ABCD2345"
                autoComplete="off"
                required
              />
              <div className="hint">
                {t("Eight characters. Ask an owner of the tenant for it.")}
              </div>
            </div>
          )}

          <button className="primary" style={{ width: "100%", marginTop: 4 }} disabled={busy}>
            {busy ? t("Working…") : mode === "create" ? t("Create tenant") : t("Join")}
          </button>

          <div className="login-foot">
            {mode === "create" ? (
              <>
                {t("Invited to an existing tenant?")}{" "}
                <a href="#join" onClick={(e) => { e.preventDefault(); setMode("join"); }}>
                  {t("Join with a code")}
                </a>
              </>
            ) : (
              <>
                {t("Starting fresh?")}{" "}
                <a href="#create" onClick={(e) => { e.preventDefault(); setMode("create"); }}>
                  {t("Create a tenant")}
                </a>
              </>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
