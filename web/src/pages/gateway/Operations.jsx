// Three things an operator does to a gateway they already own, side by side on
// the detail page: roll the configuration back, run one tool by hand, and push
// the traffic log somewhere else.
//
// They share a file because they share a rule: none of them is a new kind of
// write. A restore appends one event, a playground call is an ordinary proxied
// call with the operator as the subject, and a webhook is configuration the log
// already records.

import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useT } from "../../i18n.jsx";
import {
  Badge, Bone, Details, Drawer, Empty, Notice, outcomeLabel, relative,
} from "../../ui.jsx";

/** "v7 · gateway.updated · 3 days ago", one line per fact. */
export function HistoryPanel({ gateway, onRestored }) {
  const t = useT();
  const [history, setHistory] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const data = await api.history(gateway.id);
      setHistory(data.history);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
  }, [gateway.id]);

  async function restore(version) {
    setBusy(true);
    setError("");
    try {
      await api.restore(gateway.id, version);
      setPreview(null);
      await load();
      onRestored?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Notice kind="err">{error}</Notice>
      {history === null ? (
        <Bone height={120} radius={10} />
      ) : history.length === 0 ? (
        <Empty title={t("No history yet")}>{t("Every change to this gateway lands here.")}</Empty>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("Version")}</th>
                <th>{t("Change")}</th>
                <th>{t("Actor")}</th>
                <th className="num">{t("When")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().map((entry) => (
                <tr key={entry.sequence}>
                  <td className="num mono">{entry.sequence}</td>
                  <td className="mono">{entry.event_type}</td>
                  <td className="hint mono">{entry.actor || t("system")}</td>
                  <td className="num hint">{relative(entry.occurred_at)}</td>
                  <td className="actions">
                    {/* The newest version is what the page already shows, so a
                        "roll back to here" button on it would only ever 422. */}
                    {entry.sequence !== history[history.length - 1].sequence && (
                      <button
                        className="ghost sm"
                        disabled={busy}
                        onClick={async () => {
                          try {
                            setPreview(await api.snapshot(gateway.id, entry.sequence));
                          } catch (err) {
                            setError(err.message);
                          }
                        }}
                      >
                        {t("preview")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {preview && (
        <Drawer
          title={t("Restore version {version}", { version: preview.version })}
          sub={t("Credentials and keys are not restored: a revoked key stays revoked.")}
          onClose={() => setPreview(null)}
          actions={
            <button
              className="primary sm"
              disabled={busy}
              onClick={() => restore(preview.version)}
            >
              {busy ? t("Restoring…") : t("Restore this version")}
            </button>
          }
        >
          <Details
            rows={[
              [t("Name"), preview.name],
              [t("Target URL"), <span className="mono">{preview.target_url}</span>],
              [t("Description"), preview.description || "—"],
              [t("Requests / minute"), preview.requests_per_minute || t("No limit")],
              [
                t("Tools"),
                preview.tools.length
                  ? preview.tools.join(", ")
                  : t("Every tool the upstream serves"),
              ],
            ]}
          />
        </Drawer>
      )}
    </>
  );
}

export function PlaygroundPanel({ gateway, onCalled }) {
  const t = useT();
  // The allowlist is the right list to offer: a tool outside it is refused
  // before the upstream sees it, so a picker built from the upstream catalog
  // would mostly offer buttons that 403.
  const tools = gateway.tools ?? [];
  const [tool, setTool] = useState(tools[0] ?? "echo");
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setTool(tools[0] ?? "echo");
  }, [gateway.id]);

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      let parsed = {};
      try {
        parsed = args.trim() ? JSON.parse(args) : {};
      } catch {
        // A 422 is the server's answer, and it names the field. Sending the raw
        // string would let the upstream explain a JSON error instead.
        setResult({ ok: false, status: 422, error: t("Arguments must be a JSON object.") });
        return;
      }
      setResult(await api.callTool(gateway.id, tool, parsed));
      onCalled?.();
    } catch (err) {
      setResult({ ok: false, status: 0, error: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="row" style={{ gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div className="field inline">
          <label htmlFor="playground-tool">{t("Tool")}</label>
          <input
            id="playground-tool"
            className="mono"
            list="playground-tools"
            value={tool}
            onChange={(event) => setTool(event.target.value)}
          />
          <datalist id="playground-tools">
            {tools.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
        <button className="primary sm" disabled={busy} onClick={run}>
          {busy ? t("Calling…") : t("Call tool")}
        </button>
      </div>
      <div className="field" style={{ marginTop: 8 }}>
        <label htmlFor="playground-args">{t("Arguments (JSON)")}</label>
        <textarea
          id="playground-args"
          className="mono"
          rows={4}
          value={args}
          onChange={(event) => setArgs(event.target.value)}
        />
      </div>
      {result && (
        <div style={{ marginTop: 10 }}>
          <Badge kind={result.ok ? "ok" : "err"}>
            {result.ok
              ? t("Answered in {ms} ms", { ms: result.duration_ms })
              : `${result.status} · ${result.reason ?? t("error")}`}
          </Badge>
          <pre className="mono" style={{ marginTop: 8, overflowX: "auto" }}>
            {result.ok ? JSON.stringify(result.result, null, 2) : result.error}
          </pre>
        </div>
      )}
      <p className="hint" style={{ marginBottom: 0 }}>
        {t("This runs the tool on the upstream with the gateway's own credential, and the call appears in the traffic log under your account. Rate limits, the allowlist and the audit trail all apply.")}
      </p>
    </>
  );
}

export function WebhooksPanel({ gateway }) {
  const t = useT();
  const [hooks, setHooks] = useState(null);
  const [form, setForm] = useState({ url: "", secret: "", outcomes: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(null); // {hook, deliveries}

  async function load() {
    try {
      const data = await api.webhooks(gateway.id);
      setHooks(data.webhooks);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
  }, [gateway.id]);

  async function add(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.addWebhook(gateway.id, {
        url: form.url,
        secret: form.secret,
        outcomes: form.outcomes.split(/[\s,]+/).filter(Boolean),
      });
      setForm({ url: "", secret: "", outcomes: "" });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function showDeliveries(hook) {
    try {
      const data = await api.webhookDeliveries(gateway.id, hook.id);
      setOpen({ hook, deliveries: data.deliveries });
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <Notice kind="err">{error}</Notice>
      {hooks === null ? (
        <Bone height={80} radius={10} />
      ) : hooks.length === 0 ? (
        <Empty title={t("No webhooks")}>
          {t("A webhook POSTs the traffic log to a URL you choose, signed with a secret only the two of you hold.")}
        </Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>{t("URL")}</th>
              <th>{t("Outcomes")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {hooks.map((hook) => (
              <tr key={hook.id}>
                <td className="mono" style={{ wordBreak: "break-all" }}>{hook.url}</td>
                <td>
                  {hook.outcomes.length ? (
                    <div className="row" style={{ gap: 4 }}>
                      {hook.outcomes.map((outcome) => (
                        <Badge key={outcome} kind="mute">
                          {outcomeLabel(t, outcome)}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="hint">{t("every outcome")}</span>
                  )}
                </td>
                <td className="actions">
                  <button className="ghost sm" onClick={() => showDeliveries(hook)}>
                    {t("deliveries")}
                  </button>
                  <button
                    className="ghost sm danger"
                    onClick={() => api.removeWebhook(gateway.id, hook.id).then(load)}
                  >
                    {t("remove")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={add} style={{ marginTop: 14 }}>
        <div className="field-row">
          <div className="field">
            <label htmlFor="hook-url">{t("URL")}</label>
            <input
              id="hook-url"
              className="mono"
              placeholder="https://example.com/hooks/omg"
              value={form.url}
              onChange={(event) => setForm({ ...form, url: event.target.value })}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="hook-secret">{t("Signing secret")}</label>
            <input
              id="hook-secret"
              type="password"
              className="mono"
              value={form.secret}
              onChange={(event) => setForm({ ...form, secret: event.target.value })}
              required
            />
          </div>
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="hook-outcomes">{t("Outcomes")}</label>
            <input
              id="hook-outcomes"
              className="mono"
              placeholder={t("leave blank for every outcome")}
              value={form.outcomes}
              onChange={(event) => setForm({ ...form, outcomes: event.target.value })}
            />
            <div className="hint">
              {t("Space-separated: allowed, unauthenticated, forbidden, rate_limited, error.")}
            </div>
          </div>
          <div className="field inline">
            <button className="primary sm" disabled={busy}>
              {busy ? t("Saving…") : t("Add webhook")}
            </button>
          </div>
        </div>
        <p className="hint" style={{ marginBottom: 0 }}>
          {t("Each POST carries X-OMG-Signature: the HMAC-SHA256 of the body with your secret. A receiver can verify it without asking us anything.")}
        </p>
      </form>

      {open && (
        <Drawer
          title={t("Deliveries")}
          sub={open.hook.url}
          onClose={() => setOpen(null)}
          actions={
            <button className="ghost sm" onClick={() => setOpen(null)}>
              {t("Close")}
            </button>
          }
        >
          {open.deliveries.length === 0 ? (
            <Empty title={t("Nothing delivered yet")}>
              {t("No matching event since this hook was created.")}
            </Empty>
          ) : (
            <table>
              <tbody>
                {open.deliveries.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Badge kind={row.status === "delivered" ? "ok" : "err"}>
                        {row.status === "delivered"
                          ? t("delivered")
                          : t("failed")}
                      </Badge>
                    </td>
                    <td className="mono hint">{row.event}</td>
                    <td className="num hint">{row.attempts}</td>
                    <td className="hint">
                      {row.status_code ?? (row.error || "—")}
                    </td>
                    <td className="hint">{relative(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Drawer>
      )}
    </>
  );
}
