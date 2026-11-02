import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Notice } from "../ui.jsx";

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.4 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-2.8-.4-4.1H24v9.3h12.5c-.3 2.1-1.6 5.2-4.6 7.3l7.6 5.9c4.5-4.2 6.6-10.3 6.6-18.4z" />
      <path fill="#FBBC05" d="M10.4 28.7c-.5-1.5-.8-3.1-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.5 0 20.1 0 24s1 7.5 2.6 10.8l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.5-5.8l-7.6-5.9c-2.1 1.4-4.8 2.4-7.9 2.4-6.3 0-11.7-3.9-13.6-9.4l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
    </svg>
  );
}

export function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [methods, setMethods] = useState({ password: true, google: false });

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
    <div className="login-wrap">
      <div className="login">
        <div className="logo">
          <div className="logo-mark" />
          <div className="logo-text">
            OpenMCP<span>Gateway</span>
          </div>
        </div>
        <form className="card" onSubmit={submit}>
          <h2 style={{ marginBottom: 4 }}>
            {mode === "login" ? "Sign in" : "Create an account"}
          </h2>
          <p className="hint" style={{ marginBottom: 16 }}>
            {mode === "login"
              ? "Operator access to gateways, keys and traffic."
              : "Then create your own tenant or join one you were invited to."}
          </p>
          <Notice kind="err">{error}</Notice>
          <div className="field">
            <label htmlFor="email">Email</label>
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
            <label htmlFor="password">Password</label>
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
              <div className="hint" style={{ marginTop: 5 }}>
                At least 12 characters.
              </div>
            )}
          </div>
          <button className="primary" style={{ width: "100%", marginTop: 4 }} disabled={busy}>
            {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
          {methods.google && (
            <>
              <div className="divider">
                <span>or</span>
              </div>
              {/* A plain link, not a fetch: the login is a browser redirect to
                  Google and back, so the browser must own the navigation. */}
              <a className="btn google" href="/api/auth/google/start">
                <GoogleMark />
                Continue with Google
              </a>
            </>
          )}
        </form>
        <div className="login-foot">
          {mode === "login" ? (
            <>
              New here?{" "}
              <a href="#register" onClick={(e) => { e.preventDefault(); setMode("register"); }}>
                Create an account
              </a>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <a href="#login" onClick={(e) => { e.preventDefault(); setMode("login"); }}>
                Sign in
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
