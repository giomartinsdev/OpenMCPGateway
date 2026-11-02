import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, Notice, can } from "../ui.jsx";

const SAMPLE = `gateways:
  - slug: rancher
    name: "Rancher cluster"
    target_url: http://rancher-mcp.apps.svc.cluster.local:8000/mcp
    auth:
      mode: oauth
      idp: github
      client_id: \${GITHUB_CLIENT_ID}
      client_secret: \${GITHUB_CLIENT_SECRET}
    rate_limit: 120
    allow: ["github:giomartinsdev"]
    api_keys: ["ci"]

prune: false
`;

export function ConfigPage({ me }) {
  const canEdit = can(me, "config:write");
  const [yaml, setYaml] = useState("");
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.exportConfig()
      .then((data) => setYaml(data.yaml))
      .catch((err) => setBanner({ kind: "err", text: err.message }));
  }, []);

  async function run(action) {
    setBusy(true);
    setBanner(null);
    try {
      const result = await action(yaml);
      setBanner({ kind: "ok", text: JSON.stringify(result) });
      if (action === api.applyConfig) {
        const fresh = await api.exportConfig();
        setYaml(fresh.yaml);
      }
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
          <h1>Config as code</h1>
          <div className="sub">
            The same commands the UI issues, in a file you can commit and review.
          </div>
        </div>
        <div className="row">
          <button onClick={() => setYaml(SAMPLE)} disabled={busy}>Load example</button>
          <button onClick={() => run(api.validateConfig)} disabled={busy}>Validate</button>
          {canEdit && (
            <button className="primary" onClick={() => run(api.applyConfig)} disabled={busy}>
              {busy ? "Working…" : "Apply"}
            </button>
          )}
        </div>
      </div>

      <Notice kind={banner?.kind ?? "info"}>
        {banner?.text ||
          "Validate parses and resolves placeholders without touching state. Apply runs the commands."}
      </Notice>

      <Card>
        <textarea
          value={yaml}
          onChange={(event) => setYaml(event.target.value)}
          spellCheck={false}
          style={{ minHeight: 420 }}
        />
      </Card>

      <Card title="How this stays honest">
        <p className="hint" style={{ margin: 0 }}>
          Both this file and the UI end up in the same command handler, so a gateway
          created in the browser can be exported here, edited, and applied back. Secrets
          are never exported: write <span className="mono">${"{ENV_VAR}"}</span> and the
          server reads them from its own environment.
        </p>
      </Card>
    </>
  );
}
