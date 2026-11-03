import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useI18n, useT } from "../i18n.jsx";
import {
  Badge, Bone, Card, Empty, Endpoint, Notice, Skeleton, Stat, can, relative,
} from "../ui.jsx";
import { navigate } from "../App.jsx";
import { ConnectGuide } from "./gateway/ConnectGuide.jsx";
import { ToolsPanel } from "./gateway/ToolsPanel.jsx";
import {
  AuthForm, RateLimitForm, RecentRequests, Traffic,
} from "./gateway/Panels.jsx";

// Placeholder for the detail page: one object with the shape the page reads, so
// the skeleton is the page's own layout rather than a grey rectangle.
const SAMPLE = {
  id: "gw_0000",
  name: "Gateway name",
  slug: "slug",
  description: "What this server is for",
  endpoint: "https://gateway.example.com/g/slug/mcp",
  target_url: "http://upstream.svc.cluster.local:8000/mcp",
  auth_mode: "oauth",
  idp: "google",
  enabled: true,
  requests_per_minute: 120,
  client_id: "…apps.googleusercontent.com",
  principals: [{ subject: "someone@example.com", added_at: new Date().toISOString() }],
  api_keys: [{ label: "default", key_id: "key_0000", issued_at: new Date().toISOString() }],
  tools: [],
  requests: [],
  stats: { requests: 0, previous_requests: 0, avg_duration_ms: 0, max_duration_ms: 0, by_ip: {}, by_outcome: {}, by_gateway: {}, series: [], busiest: null },
};

/** The eye: one click, one plaintext, one time. */
function EyeIcon() {
  return (
    <svg
      width="15" height="15" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M1.4 8s2.4-4.4 6.6-4.4S14.6 8 14.6 8s-2.4 4.4-6.6 4.4S1.4 8 1.4 8Z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

export function GatewayDetail({ id, me }) {
  const t = useT();
  const { locale } = useI18n();
  // Two separate permissions: managing a gateway is not the same as minting a
  // credential for it, and the server checks them separately.
  const canEdit = can(me, "gateways:write");
  const canKeys = can(me, "keys:write");
  const [gateway, setGateway] = useState(null);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null); // {kind, text}
  // Holds a plaintext only between the response and the operator's clipboard.
  const [newKey, setNewKey] = useState("");
  // Which key's plaintext is on screen, and why one cannot be shown. The eye is
  // per row: a revealed key is a fact about that key, not about the page.
  const [shownKey, setShownKey] = useState("");

  async function load() {
    try {
      setGateway(await api.gateway(id));
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [id]);

  if (error) return <Notice kind="err">{error}</Notice>;

  const stats = gateway?.stats ?? {};
  const header = gateway ?? SAMPLE;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <a href="#/">{t("Gateways")}</a>
            <span className="sep">/</span>
            <span>{header.slug}</span>
          </div>
          <Skeleton data={gateway} sample={SAMPLE} label={t("Loading gateway")}>
            {(data) => <h1>{data.name}</h1>}
          </Skeleton>
          <div className="sub">{header.description || t("No description.")}</div>
        </div>
        <div className="actions">
          <Badge kind={header.enabled ? "ok" : "mute"}>
            {header.enabled ? t("enabled") : t("disabled")}
          </Badge>
          {gateway && canEdit && (
            <button
              onClick={async () => {
                await (gateway.enabled ? api.disable(id) : api.enable(id));
                load();
              }}
            >
              {gateway.enabled ? t("Disable") : t("Enable")}
            </button>
          )}
        </div>
      </div>

      {banner && <Notice kind={banner.kind}>{banner.text}</Notice>}

      {gateway?.pending && (
        <Notice kind="err">
          {t("This gateway is enabled but not serving yet: {reason}. Set the OAuth client details below and it goes live immediately.", {
            reason: gateway.pending,
          })}
        </Notice>
      )}

      <div className="grid-3" style={{ marginBottom: 12 }}>
        <Skeleton data={gateway} sample={SAMPLE} label={t("Loading requests")}>
          {(data) => (
            <Stat label={t("Requests / 60 min")} value={data.stats?.requests ?? 0} />
          )}
        </Skeleton>
        <Skeleton data={gateway} sample={SAMPLE} label={t("Loading latency")}>
          {(data) => (
            <Stat
              label={t("Avg latency")}
              value={`${data.stats?.avg_duration_ms ?? 0} ms`}
              hint={t("peak {ms} ms", { ms: data.stats?.max_duration_ms ?? 0 })}
            />
          )}
        </Skeleton>
        <Skeleton data={gateway} sample={SAMPLE} label={t("Loading callers")}>
          {(data) => (
            <Stat
              label={t("Distinct callers")}
              value={Object.keys(data.stats?.by_ip ?? {}).length}
            />
          )}
        </Skeleton>
      </div>

      <Card title={t("Endpoint")}>
        {gateway ? (
          <>
            <Endpoint value={gateway.endpoint} />
            <p className="hint" style={{ marginTop: 10 }}>
              {t("Point an MCP client here. Upstream stays private at")}{" "}
              <span className="mono">{gateway.target_url}</span>.
            </p>
          </>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            <Bone height={38} radius={6} />
            <Bone height={12} width="70%" />
          </div>
        )}
      </Card>

      {gateway ? <ConnectGuide gateway={gateway} /> : null}

      <Card title={t("Auth")}>
        <div className="grid-2">
          <div>
            <div className="row" style={{ gap: 24, marginBottom: 12 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>{t("Mode")}</div>
                <Badge kind={header.auth_mode === "none" ? "warn" : "info"}>
                  {header.auth_mode}
                  {header.idp ? ` · ${header.idp}` : ""}
                </Badge>
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>{t("Allowlist")}</div>
                <Badge kind={header.principals.length ? "ok" : "mute"}>
                  {header.principals.length
                    ? t("{count} principals", { count: header.principals.length })
                    : t("any authenticated subject")}
                </Badge>
              </div>
            </div>
            <p className="hint" style={{ margin: 0 }}>
              {t("An allowlist is a second gate: a valid token still has to belong to a listed subject. Empty means \"anyone who authenticates\".")}
            </p>
          </div>
          {gateway ? (
            canEdit ? (
              <AuthForm gateway={gateway} onSaved={(msg) => { setBanner(msg); load(); }} />
            ) : (
              <Notice kind="info">
                {t("Read-only: an administrator of this tenant changes the auth.")}
              </Notice>
            )
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              <Bone height={34} />
              <Bone height={34} />
              <Bone height={34} />
            </div>
          )}
        </div>
      </Card>

      <Card title={t("Rate limit")}>
        {gateway ? (
          canEdit ? (
            <RateLimitForm gateway={gateway} onSaved={load} />
          ) : (
            <Notice kind="info">
              {gateway.requests_per_minute
                ? t("{count} requests per minute per caller.", {
                    count: gateway.requests_per_minute,
                  })
                : t("No limit. Each caller gets its own budget.")}
            </Notice>
          )
        ) : (
          <Bone height={34} width={320} />
        )}
      </Card>

      <Card title={t("Tools")}>
        {gateway ? (
          <ToolsPanel gateway={gateway} me={me} onSaved={load} />
        ) : (
          <Bone height={120} radius={10} />
        )}
      </Card>

      <div className="grid-2" style={{ marginTop: 12 }}>
        <Card
          title={t("Allowed subjects")}
          actions={gateway && canEdit && (
            <button
              className="sm"
              onClick={() => {
                const subject = window.prompt(t("Subject to allow (e.g. octocat):"));
                if (!subject) return;
                api.addPrincipal(id, subject).then(load).catch((e) => setError(e.message));
              }}
            >
              {t("Add")}
            </button>
          )}
        >
          {header.principals.length === 0 ? (
            gateway ? (
              <Empty title={t("No allowlist")}>
                {t("Every authenticated caller is accepted.")}
              </Empty>
            ) : (
              <Bone height={60} radius={10} />
            )
          ) : (
            <table>
              <tbody>
                {header.principals.map((principal) => (
                  <tr key={principal.subject}>
                    <td className="mono">{principal.subject}</td>
                    <td className="num hint">{relative(principal.added_at)}</td>
                    {canEdit && (
                      <td className="actions">
                        <button
                          className="ghost sm"
                          onClick={() => api.removePrincipal(id, principal.subject).then(load)}
                          disabled={!gateway}
                        >
                          {t("remove")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card
          title={t("API keys")}
          actions={gateway && canKeys && (
            <button
              className="sm"
              onClick={() => {
                api.issueKey(id, "default")
                  .then((issued) => {
                    setNewKey(issued.api_key);
                    load();
                  })
                  .catch((e) => setError(e.message));
              }}
            >
              {t("Issue key")}
            </button>
          )}
        >
          {/* Two moments produce a plaintext: minting one, and using the eye on a
              key that was never read. Both land here, next to the table they
              belong to, because a banner at the top of a long page is a secret
              people scroll past. */}
          {shownKey && (
            <div className="secret" style={{ marginBottom: 12 }}>
              <div className="spread" style={{ marginBottom: 6 }}>
                <strong>{t("Last chance: this key cannot be shown again.")}</strong>
                <button className="ghost sm" onClick={() => setShownKey("")}>
                  {t("dismiss")}
                </button>
              </div>
              <div className="row">
                <span className="mono" style={{ wordBreak: "break-all" }}>{shownKey}</span>
                <button
                  className="ghost copy"
                  onClick={() => navigator.clipboard?.writeText(shownKey)}
                >
                  {t("copy")}
                </button>
              </div>
            </div>
          )}
          {newKey && (
            <div className="secret" style={{ marginBottom: 12 }}>
              <div className="spread" style={{ marginBottom: 6 }}>
                <strong>
                  {t("Copy this key now. You can read it once more from this page, then it is gone.")}
                </strong>
                <button className="ghost sm" onClick={() => setNewKey(null)}>
                  {t("dismiss")}
                </button>
              </div>
              <div className="row">
                <span className="mono" style={{ wordBreak: "break-all" }}>{newKey}</span>
                <button
                  className="ghost copy"
                  onClick={() => navigator.clipboard?.writeText(newKey)}
                >
                  {t("copy")}
                </button>
              </div>
            </div>
          )}
          {header.api_keys.length === 0 ? (
            gateway ? (
              <Empty title={t("No keys")}>
                {t("Issue one to authenticate with a bearer token.")}
              </Empty>
            ) : (
              <Bone height={60} radius={10} />
            )
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("Label")}</th>
                  <th>{t("Key id")}</th>
                  <th className="num">{t("Issued")}</th>
                  <th>{t("Seen")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {header.api_keys.map((key) => (
                  <tr key={key.key_id}>
                    <td>{key.label}</td>
                    {/* The id, not the subject: the subject is `api-key:<id>`
                        and pasting that as a bearer is the mistake this column
                        used to invite. */}
                    <td className="mono hint">{key.key_id}</td>
                    <td className="num hint">{relative(key.issued_at)}</td>
                    <td className="hint">
                      {key.revealed_at
                        ? t("seen {when} by {who}", {
                            when: relative(key.revealed_at),
                            who: key.revealed_by ?? "—",
                          })
                        : key.recoverable
                          ? t("never")
                          : t("not available")}
                    </td>
                    {canKeys && (
                      <td className="actions">
                        <button
                          className="ghost icon"
                          title={t("Show the key once")}
                          aria-label={t("Show the key once")}
                          disabled={!key.recoverable || !gateway || shownKey !== ""}
                          onClick={() => {
                            api.revealKey(id, key.key_id)
                              .then((revealed) => {
                                setShownKey(revealed.api_key);
                                load();
                              })
                              .catch((e) => setError(e.message));
                          }}
                        >
                          <EyeIcon />
                        </button>
                        <button
                          className="ghost sm"
                          disabled={!gateway}
                          onClick={() => {
                            if (!window.confirm(
                              t("Rotate {label}? The current key stops working immediately.", {
                                label: key.label,
                              })
                            )) return;
                            api.rotateKey(id, key.key_id)
                              .then((rotated) => {
                                setNewKey(rotated.api_key);
                                load();
                              })
                              .catch((e) => setError(e.message));
                          }}
                        >
                          {t("rotate")}
                        </button>
                        <button
                          className="ghost sm danger"
                          disabled={!gateway}
                          onClick={() => {
                            if (!window.confirm(
                              t("Revoke {label}? Callers using it get 401 at once.", {
                                label: key.label,
                              })
                            )) return;
                            api.revokeKey(id, key.key_id).then(load);
                          }}
                        >
                          {t("revoke")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Traffic stats={stats} />

      <Card title={t("Recent requests")}>
        <RecentRequests requests={gateway ? gateway.requests : null} />
      </Card>

      {gateway && canEdit && (
        <Card title={t("Danger zone")} className="danger-zone">
          <div className="spread">
            <div>
              <h3>{t("Delete this gateway")}</h3>
              <div className="hint" style={{ marginTop: 2 }}>
                {t("Removes the route and its keys immediately. The event log keeps the history.")}
              </div>
            </div>
            <button
              className="danger"
              onClick={async () => {
                if (
                  !window.confirm(
                    t("Delete {slug}? Its route stops answering.", { slug: gateway.slug }),
                  )
                )
                  return;
                await api.deleteGateway(id);
                navigate("/");
              }}
            >
              {t("Delete")}
            </button>
          </div>
        </Card>
      )}
    </>
  );
}
