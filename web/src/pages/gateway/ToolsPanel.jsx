// The tool allowlist: a second gate on *which* tools, not just who.
//
// Two sources of truth sit side by side on purpose. The catalog is what the
// upstream serves right now; the checkbox is what the gateway forwards. Showing
// only the checkboxes would let an operator tick a tool that was renamed
// upstream and never learn that nothing matches it.
//
// The catalog is fetched from the upstream through the panel, not through the
// gateway: asking the proxy would answer with the filter it is applying, which
// is exactly the thing being edited.
//
// Laid out as tiles in a responsive grid rather than a vertical list: three tools
// and three hundred use the same component, and a grid of short names says "this
// is a set you tick" where a list says "read every line".

import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useT } from "../../i18n.jsx";
import { Badge, Bone, Empty, Notice, can } from "../../ui.jsx";

// Which credential sources this gateway can admit, mirroring the domain's
// `sources`: the hosted login provider, each verifier's label, and `api_key`
// when keys are accepted. The panel only offers sources that can show up, so a
// scope can never be keyed by a typo.
export function gatewaySources(gateway) {
  const mode = gateway?.auth_mode;
  const found = [];
  if (mode === "api_key") found.push("api_key");
  if (mode === "oauth" && gateway.idp) found.push(gateway.idp);
  if (mode === "oauth" && gateway.allow_api_key) found.push("api_key");
  for (const verifier of gateway?.verifiers ?? []) {
    if (verifier.label) found.push(verifier.label);
  }
  return [...new Set(found)];
}

export function ToolsPanel({ gateway, me, onSaved }) {
  const t = useT();
  const canEdit = can(me, "gateways:write");
  // Which list is being edited: "" is the gateway-wide one every source
  // inherits, and any other value is a credential source's narrower list
  // (`google`, `api_key`, a verifier label). One card, because they are one
  // policy — splitting them into cards would hide that a source falls back.
  const sources = gatewaySources(gateway);
  const [source, setSource] = useState("");
  const scopes = gateway.tool_scopes ?? {};
  const [catalog, setCatalog] = useState(null); // {available, reason, tools}
  // Calls per tool over the last week, so the allowlist can say "nobody calls
  // this" instead of only "this is ticked". A week is the longest window the
  // stats endpoint serves, and it is enough to spot a forgotten tool.
  const [usage, setUsage] = useState(null);
  const [selected, setSelected] = useState(scopes[source] ?? gateway.tools ?? []);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const saved = scopes[source] ?? gateway.tools ?? [];

  useEffect(() => {
    const saved = (gateway.tool_scopes ?? {})[source] ?? gateway.tools ?? [];
    setSelected(saved);
    // A source that was removed from the auth block takes its list with it;
    // the selector must not keep pointing at it.
    if (source && !sources.includes(source)) setSource("");
  }, [gateway.id, gateway.tools, gateway.tool_scopes, source]);

  useEffect(() => {
    let cancelled = false;
    setCatalog(null);
    api
      .upstreamTools(gateway.id)
      .then((data) => {
        if (!cancelled) setCatalog(data);
      })
      .catch((err) => {
        if (!cancelled) setCatalog({ available: false, reason: err.message, tools: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [gateway.id]);

  useEffect(() => {
    let cancelled = false;
    setUsage(null);
    api
      .stats(gateway.id, 10080)
      .then((data) => {
        if (!cancelled) setUsage(data.by_tool ?? {});
      })
      // Usage is a hint, not a requirement: the allowlist still works when the
      // stats query fails, so a failure here stays quiet.
      .catch(() => {
        if (!cancelled) setUsage({});
      });
    return () => {
      cancelled = true;
    };
  }, [gateway.id]);

  const names = (catalog?.tools ?? []).map((tool) => tool.name);
  // A name in the allowlist that the upstream no longer serves still has to be
  // visible: it is the difference between "off" and "gone", and silently
  // dropping it on save would erase a decision the operator made.
  const missing = selected.filter((name) => catalog?.available && !names.includes(name));
  const rows = [...names, ...missing];
  const visible = rows.filter((name) =>
    name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const all = selected.length === 0;
  const dirty = JSON.stringify([...selected].sort()) !== JSON.stringify([...saved].sort());
  // A source with no list of its own is showing the gateway's, which is the
  // truth worth stating: "this is not its own list" and "this list is empty"
  // are different facts.
  const inherited = Boolean(source) && scopes[source] === undefined;

  function toggle(name) {
    setSelected((current) =>
      current.includes(name)
        ? current.filter((entry) => entry !== name)
        : [...current, name],
    );
  }

  async function save(tools) {
    setBusy(true);
    setError("");
    try {
      // Empty is one meaning per level: "all tools" for the gateway, "inherit
      // the gateway's" for a source. Both are the absence of a list.
      await api.setGatewayTools(gateway.id, tools, source);
      onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // "All" means the empty list, but ticking a box out of "all" has to start from
  // the full list, or the first click would mean "only this one".
  function tick(name, on) {
    const base = all ? names : selected;
    setSelected(on ? [...base, name] : base.filter((entry) => entry !== name));
  }

  return (
    <div className="tools">
      <div className="tools-head">
        <div className="row" style={{ gap: 8 }}>
          {sources.length > 0 && (
            <select
              className="sm"
              aria-label={t("Credential source")}
              value={source}
              onChange={(event) => setSource(event.target.value)}
              disabled={busy}
            >
              <option value="">{t("Every source")}</option>
              {sources.map((entry) => (
                <option key={entry} value={entry}>
                  {t("{source} only", { source: entry })}
                </option>
              ))}
            </select>
          )}
          <Badge kind={all ? "mute" : "info"}>
            {all
              ? t("Every tool the upstream serves")
              : t("{count} of {total} tools", {
                  count: selected.length,
                  total: rows.length,
                })}
          </Badge>
          {inherited && <Badge kind="warn">{t("inherits the gateway's list")}</Badge>}
          {canEdit && catalog?.available && rows.length > 0
            && (source ? scopes[source] !== undefined : !all) && (
            <button
              type="button"
              className="ghost sm"
              disabled={busy}
              onClick={() => save([])}
            >
              {source ? t("Inherit again") : t("Allow all")}
            </button>
          )}
        </div>
        {catalog?.available && rows.length > 0 && (
          <input
            type="search"
            className="tools-search"
            placeholder={t("Filter tools")}
            aria-label={t("Filter tools")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        )}
      </div>

      <Notice kind="err">{error}</Notice>

      {catalog === null && <Bone height={120} radius={10} />}

      {catalog && !catalog.available && (
        <Notice kind="warn">
          {t("Could not read the upstream's catalog, so the list is the one already configured. New tools are allowed by default until you set a list.")}
          <div className="hint" style={{ marginTop: 6 }}>{catalog.reason}</div>
        </Notice>
      )}

      {catalog?.available && rows.length === 0 && (
        <Empty title={t("The upstream serves no tools")}>
          {t("Nothing to allowlist. Add tools to the MCP server first.")}
        </Empty>
      )}

      {visible.length > 0 && (
        <ul className="tools-list">
          {visible.map((name) => {
            const entry = catalog?.tools.find((tool) => tool.name === name);
            const on = all || selected.includes(name);
            const gone = !entry;
            // Only claim "unused" once the usage numbers actually arrived: an
            // empty map from a failed request is not the same as zero calls.
            const calls = usage?.[name];
            return (
              <li key={name} className={gone ? "gone" : ""}>
                <label title={entry?.description ?? undefined}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!canEdit || busy}
                    onChange={() => tick(name, !on)}
                  />
                  <span className="tool-name mono">{name}</span>
                  {gone && <Badge kind="warn">{t("not served upstream")}</Badge>}
                  {!gone && usage && !calls && (
                    <Badge kind="mute">{t("no calls in 7 days")}</Badge>
                  )}
                  {!gone && calls ? (
                    <span className="hint">{t("{count} calls in 7 days", { count: calls })}</span>
                  ) : null}
                  {entry?.description ? (
                    <span className="tool-desc">{entry.description}</span>
                  ) : null}
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {visible.length === 0 && rows.length > 0 ? (
        <Empty title={t("No tool matches {query}", { query })}>
          {t("Clear the search box to see the whole catalog.")}
        </Empty>
      ) : null}

      {inherited && rows.length > 0 ? (
        <p className="hint">
          {t("This source has no list of its own: it inherits the gateway's. Saving the tiles below gives it one.")}
        </p>
      ) : null}

      {canEdit ? (
        <div className="tools-foot">
          <span className="hint">
            {t("A tool outside the list is refused with 403, and disappears from tools/list.")}
          </span>
          <button
            className="primary sm"
            disabled={busy || !dirty}
            onClick={() => save(selected)}
          >
            {busy ? t("Saving…") : t("Save allowlist")}
          </button>
        </div>
      ) : (
        <Notice kind="info">
          {t("Read-only: an administrator of this tenant changes which tools are exposed.")}
        </Notice>
      )}
    </div>
  );
}
