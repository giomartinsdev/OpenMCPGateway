import { useState } from "react";
import { api } from "../api.js";
import { Notice } from "../ui.jsx";

export function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "register") await api.register(email, password);
      onLogin(await api.login(email, password));
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
            {mode === "login" ? "Sign in" : "Create the first account"}
          </h2>
          <p className="hint" style={{ marginBottom: 16 }}>
            {mode === "login"
              ? "Operator access to gateways, keys and traffic."
              : "The first account becomes an administrator."}
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
        </form>
        <div className="login-foot">
          {mode === "login" ? (
            <>
              Fresh install?{" "}
              <a href="#register" onClick={(e) => { e.preventDefault(); setMode("register"); }}>
                Create the first account
              </a>
            </>
          ) : (
            <>
              Already set up?{" "}
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
