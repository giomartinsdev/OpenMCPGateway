import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Bone, Card, Notice, can } from "../ui.jsx";

const SAMPLE = `gateways:
  - slug: rancher
    name: "Rancher cluster"
    target_url: http://rancher-mcp.support.svc.cluster.local:8000/mcp
    auth:
      mode: oauth
      idp: github
      client_id: \${GITHUB_CLIENT_ID}
      client_secret: \${GITHUB_CLIENT_SECRET}
    rate_limit: 120
    allow: ["giomartinsdev"]
    api_keys: ["ci"]

roles:
  - name: Auditor
    permissions: [gateways:read, traffic:read, audit:read]

members:
  - email: auditor@example.com
    password: \${MEMBER_PASSWORD}
    roles: [Auditor]

prune: false
`;

/** One line per thing that changed. The raw result is a nested object; printing
 *  it as JSON makes it look like an API dump instead of a diff. */
function summarize(result) {
  const lines = [];
  for (const key of ["created", "updated", "pruned"]) {
    if (result[key]?.length) lines.push(`${key}: ${result[key].join(", ")}`);
  }
  for (const section of ["roles", "members"]) {
    for (const key of ["created", "updated"]) {
      const names = result[section]?.[key] ?? [];
      if (names.length) lines.push(`${key} ${section}: ${names.join(", ")}`);
    }
  }
  for (const entry of result.api_keys ?? []) {
    lines.push(`key ${entry.gateway}/${entry.label}: ${entry.key}`);
  }
  for (const error of result.errors ?? []) lines.push(`error: ${error}`);
  if (!lines.length) lines.push("nothing changed");
  return lines.join("\n");
}

export function ConfigPage({ me }) {
  const canEdit = can(me, "config:write");
  const [yaml, setYaml] = useState(null);
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
      const result = await action(yaml ?? "");
      setBanner({
        kind: result.errors?.length ? "err" : "ok",
        text: action === api.validateConfig
          ? `parsed — gateways: ${result.gateways?.join(", ") || "none"}` +
            ` · roles: ${result.roles?.join(", ") || "none"}` +
            ` · members: ${result.members?.join(", ") || "none"}` +
            ` · prune: ${result.prune}`
          : summarize(result),
      });
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
      <div className="page-head">
        <div>
          <h1>Config as code</h1>
          <div className="sub">
            The same commands the UI issues, in a file you can commit and review.
          </div>
        </div>
        <div className="actions">
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

      <Card title="gateways.yaml" aside={canEdit ? "applies on save" : "read-only"}>
        {yaml === null ? (
          <div style={{ display: "grid", gap: 8 }}>
            {[46, 32, 62, 24, 38, 54, 30, 44].map((width, i) => (
              <Bone key={i} height={11} width={`${width}%`} />
            ))}
          </div>
        ) : (
          <textarea
            value={yaml}
            onChange={(event) => setYaml(event.target.value)}
            spellCheck={false}
            style={{ minHeight: 460 }}
            aria-label="Gateway configuration"
          />
        )}
      </Card>

      <div className="grid-2">
        <Card title="What the file can declare">
          <p className="hint" style={{ margin: 0 }}>
            <span className="mono">gateways</span> — slug, target, auth, limits,
            allowlist and API keys.
            <br />
            <span className="mono">roles</span> — a name and a permission list. Built-ins
            (<span className="mono">owner</span>, <span className="mono">viewer</span>)
            can be named but not redefined.
            <br />
            <span className="mono">members</span> — an email, the roles to hold, and a
            password from <span className="mono">${"{ENV_VAR}"}</span> that is only used
            when the account is new. A member whose roles already match is left alone.
          </p>
        </Card>
        <Card title="How this stays honest">
          <p className="hint" style={{ margin: 0 }}>
            Both this file and the UI end up in the same command handler, so a gateway
            created in the browser can be exported here, edited, and applied back. The
            file is authoritative for what it declares: anything left out survives, so a
            typo cannot delete production. <span className="mono">prune: true</span> opts
            into deleting gateways the file omits — it never deletes members, because an
            account missing from a file is far more likely to be a merge accident.
          </p>
        </Card>
      </div>
    </>
  );
}
