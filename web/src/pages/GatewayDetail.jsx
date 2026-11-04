import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useT } from "../i18n.jsx";
import {
  AuthBadge, Badge, Bone, Card, Empty, Endpoint, Notice, Skeleton, Stat, can, relative,
} from "../ui.jsx";
import { navigate } from "../App.jsx";
import { ConnectGuide } from "./gateway/ConnectGuide.jsx";
import { ToolsPanel } from "./gateway/ToolsPanel.jsx";
import { AuthForm, QuotaForm, RateLimitForm, Requests, Traffic } from "./gateway/Panels.jsx";
import { HistoryPanel, WebhooksPanel } from "./gateway/Operations.jsx";

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
  api_keys: [
    {
      key_id: "key_0000",
      label: "default",
      issued_at: new Date().toISOString(),
      expires_at: null,
      revealed_at: null,
      revealed_by: null,
      recoverable: true,
    },
  ],
  tools: [],
  health: null,
  stats: { requests: 0, previous_requests: 0, avg_duration_ms: 0, max_duration_ms: 0, by_ip: {}, by_outcome: {}, by_tool: {}, by_gateway: {}, by_gateway_outcome: {}, series: [], busiest: null },
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

/** The upstream's last known answer, or "not checked yet" — which is not "up". */
function UpstreamHealth({ health, testing }) {
  const t = useT();
  if (testing || !health) {
    return (
      <Badge kind="mute">
        {testing ? t("Testing…") : t("Upstream not checked yet")}
      </Badge>
    );
  }
  return (
    <>
      <Badge kind={health.up ? "ok" : "err"}>
        {health.up
          ? t("Upstream up · {detail}", { detail: health.detail })
          : t("Upstream down")}
      </Badge>
      {!health.up && health.detail ? (
        <span className="hint mono">{health.detail}</span>
      ) : null}
      {/* A tool that quietly changed shape breaks a client that worked
          yesterday, and nothing else on the page would say so. */}
      {health.changed?.length ? (
        <Badge kind="warn">
          {health.changed.length === 1
            ? t("1 tool changed since last check")
            : t("{count} tools changed since last check", {
                count: health.changed.length,
              })}
        </Badge>
      ) : null}
      <span className="hint">
        {t("checked {when}", { when: relative(health.checked_at) })}
      </span>
    </>
  );
}

/** "expires in 3 days" / "expired" / nothing at all for a key that never does. */
function expiryLabel(t, expiresAt) {
  if (!expiresAt) return null;
  const left = new Date(expiresAt).getTime() - Date.now();
  if (left <= 0) return { kind: "err", text: t("expired") };
  const days = Math.ceil(left / 86_400_000);
  return {
    kind: days <= 7 ? "warn" : "mute",
    text: days === 1 ? t("expires tomorrow") : t("expires in {days} days", { days }),
  };
}

export function GatewayDetail({ id, me }) {
  const t = useT();
  // Two separate permissions: managing a gateway is not the same as minting a
  // credential for it, and the server checks them separately.
  const canEdit = can(me, "gateways:write");
  const canKeys = can(me, "keys:write");
  const [gateway, setGateway] = useState(null);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState(null); // {kind, text}
  // Holds a plaintext only between the response and the operator's clipboard.
  const [newKey, setNewKey] = useState("");
  // The upstream probe result: seeded from the detail payload (last cached
  // answer), replaced by an explicit test. `null` means nobody checked yet.
  const [health, setHealth] = useState(null);
  const [testing, setTesting] = useState(false);
  // Which expiry the next issued key gets. Empty string = never.
  const [keyDays, setKeyDays] = useState("");
  // Which key's plaintext is on screen, and why one cannot be shown. The eye is
  // per row: a revealed key is a fact about that key, not about the page.
  const [shownKey, setShownKey] = useState("");
  // Requests or history in the Activity card. Requests is the tab an operator
  // opens the page for; history is the one they come back to.
  const [tab, setTab] = useState("requests");

  async function load() {
    try {
      const next = await api.gateway(id);
      setGateway(next);
      // The poll refreshes the probe result too, but only when the server has
      // one: an empty badge is honest, and "up" that was never checked is not.
      if (next.health) setHealth(next.health);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); const timer = setInterval(load, 10000); return () => clearInterval(timer); }, [id]);

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

      {/* Endpoint, the limit and the tool list are one decision — "what this
          gateway accepts and what it forwards" — so they sit together instead of
          in three cards the reader has to join by scrolling. */}
      <Card title={t("Endpoint and limits")}>
        {gateway ? (
          <>
            <Endpoint value={gateway.endpoint} />
            <p className="hint" style={{ margin: "10px 0 0" }}>
              {t("Point an MCP client here. Upstream stays private at")}{" "}
              <span className="mono">{gateway.target_url}</span>.
            </p>
            <div className="limit-row">
              {canEdit ? (
                <RateLimitForm gateway={gateway} onSaved={load} />
              ) : (
                <div className="row">
                  <Badge kind={gateway.requests_per_minute ? "info" : "mute"}>
                    {gateway.requests_per_minute
                      ? `${gateway.requests_per_minute} ${t("requests / minute")}`
                      : t("No limit")}
                  </Badge>
                  <span className="hint">
                    {t("0 means no limit. Each subject gets its own budget, and only JSON-RPC requests count — handshakes and notifications are free.")}
                  </span>
                </div>
              )}
            </div>
            <div className="limit-row">
              {canEdit ? (
                <QuotaForm gateway={gateway} onSaved={load} />
              ) : (
                <div className="row">
                  <Badge kind={gateway.monthly_quota ? "info" : "mute"}>
                    {gateway.monthly_quota
                      ? `${gateway.monthly_quota} ${t("calls / month")}`
                      : t("No monthly quota")}
                  </Badge>
                  <span className="hint">
                    {t("0 means no ceiling. The counter resets on the first day of the month, UTC, and a call over the quota is refused with 429 before it reaches the upstream.")}
                  </span>
                </div>
              )}
            </div>
            {/* "Is the gateway up" and "is the thing behind it up" are different
                questions. /healthz answers the first; this answers the second,
                because a dead upstream used to look like a quiet, healthy
                gateway. */}
            <div className="health-row">
              <UpstreamHealth health={health} testing={testing} />
              {canEdit && (
                <button
                  className="ghost sm"
                  disabled={testing}
                  onClick={async () => {
                    setTesting(true);
                    try {
                      setHealth(await api.testGateway(id));
                    } catch (err) {
                      setHealth({ up: false, detail: err.message, tools: 0 });
                    } finally {
                      setTesting(false);
                    }
                  }}
                >
                  {testing ? t("Testing…") : t("Test upstream")}
                </button>
              )}
            </div>
          </>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            <Bone height={38} radius={6} />
            <Bone height={12} width="70%" />
            <Bone height={34} width={320} />
          </div>
        )}
      </Card>

      <Card title={t("Tools")}>
        {gateway ? (
          <ToolsPanel gateway={gateway} me={me} onSaved={load} />
        ) : (
          <Bone height={120} radius={10} />
        )}
      </Card>

      <Card title={t("Auth")}>
        {gateway ? (
          <>
            <div className="row" style={{ gap: 24, marginBottom: 14 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>{t("Mode")}</div>
                <AuthBadge gateway={gateway} t={t} />
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 6 }}>{t("Allowlist")}</div>
                <Badge kind={gateway.principals.length ? "ok" : "mute"}>
                  {gateway.principals.length
                    ? t("{count} principals", { count: gateway.principals.length })
                    : t("any authenticated subject")}
                </Badge>
              </div>
            </div>
            {canEdit ? (
              <AuthForm gateway={gateway} onSaved={(msg) => { setBanner(msg); load(); }} />
            ) : (
              <Notice kind="info">
                {t("Read-only: an administrator of this tenant changes the auth.")}
              </Notice>
            )}
          </>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <Bone height={22} width={260} />
            <Bone height={34} />
            <Bone height={34} width="60%" />
          </div>
        )}
      </Card>

      <div className="grid-2">
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
          {/* The rule, once, in the card that owns the list. It used to sit in
              the Auth card, an explanation of a table two cards further down. */}
          {gateway && (
            <p className="hint" style={{ margin: "0 0 12px" }}>
              {t("An allowlist is a second gate: a valid token still has to belong to a listed subject. Empty means \"anyone who authenticates\".")}
            </p>
          )}
          {header.principals.length === 0 ? (
            gateway ? (
              <Empty title={t("No allowlist")} />
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
            <div className="row" style={{ gap: 8 }}>
              {/* Default "never": making every new key expire on a guess would
                  break an integration nobody thought about. The point is that
                  the choice is visible at mint time, not after a year of drift. */}
              <select
                aria-label={t("Expires in")}
                value={keyDays}
                onChange={(event) => setKeyDays(event.target.value)}
                style={{ height: 26, fontSize: "var(--t-label)" }}
              >
                <option value="">{t("never expires")}</option>
                <option value="30">{t("in 30 days")}</option>
                <option value="90">{t("in 90 days")}</option>
                <option value="365">{t("in 365 days")}</option>
              </select>
              <button
                className="sm"
                onClick={() => {
                  api.issueKey(id, "default", keyDays ? Number(keyDays) : null)
                    .then((issued) => {
                      setNewKey(issued.api_key);
                      load();
                    })
                    .catch((e) => setError(e.message));
                }}
              >
                {t("Issue key")}
              </button>
            </div>
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
            <div className="table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>{t("Label")}</th>
                    <th>{t("Key id")}</th>
                    <th className="num">{t("Issued")}</th>
                    <th>{t("Expires")}</th>
                    <th>{t("Seen")}</th>
                    <th>{t("Last used")}</th>
                    <th><span className="sr-only">{t("Operations")}</span></th>
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
                        {(() => {
                          const expiry = expiryLabel(t, key.expires_at);
                          return expiry ? (
                            <Badge kind={expiry.kind}>{expiry.text}</Badge>
                          ) : (
                            t("never")
                          );
                        })()}
                      </td>
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
                      {/* Read from the traffic log as far back as retention
                          goes. "Never" here means "not since we started keeping
                          the evidence", which is the only honest claim to make. */}
                      <td className="hint">
                        {key.last_used_at ? relative(key.last_used_at) : t("never")}
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
            </div>
          )}
        </Card>
      </div>

      {gateway ? <ConnectGuide gateway={gateway} /> : null}

      <Traffic stats={stats} />

      <Card title={t("Webhooks")}>
        {gateway ? (
          <WebhooksPanel gateway={gateway} />
        ) : (
          <Bone height={120} radius={10} />
        )}
      </Card>

      {/* One card, two tabs: the two lists answer "what happened here", and
          stacked they pushed the danger zone a full screen down. */}
      <Card
        title={t("Activity")}
        actions={
          <div className="seg" role="tablist" aria-label={t("Activity")}>
            <button
              role="tab"
              aria-selected={tab === "requests"}
              className={tab === "requests" ? "active" : ""}
              onClick={() => setTab("requests")}
            >
              {t("Requests")}
            </button>
            <button
              role="tab"
              aria-selected={tab === "history"}
              className={tab === "history" ? "active" : ""}
              onClick={() => setTab("history")}
            >
              {t("History")}
            </button>
          </div>
        }
      >
        {gateway ? (
          tab === "requests" ? (
            <Requests gateway={gateway} />
          ) : (
            <HistoryPanel gateway={gateway} onRestored={load} />
          )
        ) : (
          <Bone height={140} radius={10} />
        )}
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
