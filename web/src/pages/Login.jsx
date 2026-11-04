import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useT } from "../i18n.jsx";
import { Notice } from "../ui.jsx";

function GoogleMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.4 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-2.8-.4-4.1H24v9.3h12.5c-.3 2.1-1.6 5.2-4.6 7.3l7.6 5.9c4.5-4.2 6.6-10.3 6.6-18.4z" />
      <path fill="#FBBC05" d="M10.4 28.7c-.5-1.5-.8-3.1-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.5 0 20.1 0 24s1 7.5 2.6 10.8l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.5-5.8l-7.6-5.9c-2.1 1.4-4.8 2.4-7.9 2.4-6.3 0-11.7-3.9-13.6-9.4l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
    </svg>
  );
}

/**
 * The sign-in form, with no page around it: the landing puts it in the hero and
 * onboarding puts it nowhere. Everything that changes the *session* stops here,
 * so the two screens cannot drift apart on validation or error copy.
 */
export function AuthCard({ onLogin }) {
  const t = useT();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [methods, setMethods] = useState({ password: true, google: false });
  // Where to go once signed in. Only an in-app path is honoured: a `next` taken
  // from the URL is attacker-supplied, and an absolute one is an open redirect.
  const next = (() => {
    const value = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("next");
    return value && /^\/g\/[\w-]+\//.test(value) ? value : "";
  })();

  useEffect(() => {
    // A half-configured deployment must not show a button that 503s.
    api.authMethods().then(setMethods).catch(() => setMethods({ password: true, google: false }));
    // The callback redirects back here with the reason when a login is refused.
    const reason = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("error");
    if (reason) setError(reason);
  }, []);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "register") await api.register(email, password);
      await api.login(email, password);
      if (next) {
        window.location.href = next;
        return;
      }
      // Read the identity back rather than trust the login response: roles and
      // permissions are resolved server-side, and the panel must not guess them.
      onLogin(await api.me());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card auth-card" onSubmit={submit}>
      <h2 style={{ marginBottom: 4 }}>
        {mode === "login" ? t("Welcome back") : t("Create your account")}
      </h2>
      <p className="hint" style={{ marginBottom: 16 }}>
        {mode === "login"
          ? t("Operator access to gateways, keys and traffic.")
          : t("Then create your own tenant or join one you were invited to.")}
      </p>

      <Notice kind="err">{error}</Notice>

      <div className="field">
        <label htmlFor="email">{t("Email")}</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </div>
      <div className="field">
        <label htmlFor="password">{t("Password")}</label>
        <input
          id="password"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={mode === "register" ? 12 : undefined}
          required
        />
        {mode === "register" && (
          <div className="hint" style={{ marginTop: 4 }}>
            {t("At least 12 characters.")}
          </div>
        )}
      </div>

      <button className="primary" style={{ width: "100%", marginTop: 4 }} disabled={busy}>
        {busy ? t("Working…") : mode === "login" ? t("Sign in") : t("Create account")}
      </button>

      {methods.google && (
        <>
          <div className="divider">
            <span>{t("or")}</span>
          </div>
          {/* A plain link, not a fetch: the login is a browser redirect to
              Google and back, so the browser must own the navigation. `next`
              is carried so a login started from a gateway's consent screen
              returns there instead of to the panel. */}
          <a className="btn google" href={`/api/auth/google/start${next ? `?next=${encodeURIComponent(next)}` : ""}`}>
            <GoogleMark />
            {t("Continue with Google")}
          </a>
        </>
      )}

      <div className="login-foot">
        {mode === "login" ? (
          <>
            {t("New here?")}{" "}
            <a href="#create" onClick={(e) => { e.preventDefault(); setError(""); setMode("register"); }}>
              {t("Create an account")}
            </a>
          </>
        ) : (
          <>
            {t("Already have one?")}{" "}
            <a href="#login" onClick={(e) => { e.preventDefault(); setError(""); setMode("login"); }}>
              {t("Sign in")}
            </a>
          </>
        )}
      </div>
    </form>
  );
}
