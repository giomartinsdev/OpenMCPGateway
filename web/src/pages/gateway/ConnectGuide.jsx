// "Test this gateway": the exact requests to call the proxy, per auth mode.
//
// The text comes from the server (`GET /api/gateways/{id}/connect`), not from a
// template here: the OAuth chain names endpoints the proxy itself serves, and a
// second copy in the browser is the copy that goes stale.

import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { Bone, Card, Empty, Notice } from "../../ui.jsx";

function CodeBlock({ step }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="snippet">
      <div className="snippet-head">
        <div>
          <div className="snippet-title">{step.title}</div>
          {step.description ? <div className="hint">{step.description}</div> : null}
        </div>
        <button
          className="ghost sm"
          onClick={async () => {
            await navigator.clipboard?.writeText(step.code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="code"><code>{step.code}</code></pre>
    </div>
  );
}

export function ConnectGuide({ gateway }) {
  const [tab, setTab] = useState("curl");
  const [guide, setGuide] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .connectGuide(gateway.id)
      .then((value) => alive && setGuide(value))
      .catch((err) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [gateway.id, gateway.auth_mode, gateway.updated_at]);

  const steps = guide?.[tab] ?? [];

  return (
    <Card
      title="Test this gateway"
      actions={
        <div className="seg">
          {["curl", "python"].map((name) => (
            <button
              key={name}
              className={tab === name ? "active" : ""}
              onClick={() => setTab(name)}
            >
              {name}
            </button>
          ))}
        </div>
      }
    >
      {error ? <Notice kind="err">{error}</Notice> : null}
      {!guide && !error ? (
        <div style={{ display: "grid", gap: 18 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ display: "grid", gap: 8 }}>
              <Bone height={12} width={i === 2 ? "28%" : "44%"} />
              <Bone height={54} radius={10} />
            </div>
          ))}
        </div>
      ) : null}
      {guide && steps.length === 0 ? (
        <Empty>No steps for this auth mode.</Empty>
      ) : null}
      {steps.map((step) => (
        <CodeBlock key={`${tab}-${step.title}`} step={step} />
      ))}
      {guide?.auth_mode === "oauth" ? (
        <p className="hint" style={{ marginBottom: 0 }}>
          {guide.idp ? <span className="mono">{guide.idp}</span> : "OAuth"} issues the
          token; the gateway only validates it. Steps 1–5 are what an MCP client does
          for you — a 401 on step 1 is expected, not a failure.
        </p>
      ) : null}
    </Card>
  );
}
