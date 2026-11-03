// The tool allowlist: a second gate on *which* tools, not just who.
//
// Two sources of truth sit side by side on purpose. The left column is what the
// upstream serves right now; the right is what the gateway forwards. Showing
// only the checkboxes would let an operator tick a tool that was renamed
// upstream and never learn that nothing matches it.
//
// The catalog is fetched from the upstream through the panel, not through the
// gateway: asking the proxy would answer with the filter it is applying, which
// is exactly the thing being edited.

import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useT } from "../../i18n.jsx";
import { Badge, Bone, Empty, Notice, can } from "../../ui.jsx";

export function ToolsPanel({ gateway, me, onSaved }) {
  const t = useT();
  const canEdit = can(me, "gateways:write");
  const [catalog, setCatalog] = useState(null); // {available, reason, tools}
  const [selected, setSelected] = useState(gateway.tools ?? []);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setSelected(gateway.tools ?? []);
  }, [gateway.id, gateway.tools]);

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
      await api.setGatewayTools(gateway.id, tools);
      onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tools">
      <div className="tools-head">
        <div className="row" style={{ gap: 8 }}>
          <Badge kind={all ? "mute" : "info"}>
            {all
              ? t("Every tool the upstream serves")
              : t("{count} of {total} tools", {
                  count: selected.length,
                  total: rows.length,
                })}
          </Badge>
          {!all && (
            <button className="ghost sm" disabled={busy || !canEdit}
                    onClick={() => save([])}>
              {t("Allow all")}
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
            return (
              <li key={name} className={gone ? "gone" : ""}>
                <label>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!canEdit || busy}
                    onChange={() => {
                      // Ticking one box out of "all" starts from the full list,
                      // or the first click would mean "only this one" — which is
                      // almost never what the operator meant.
                      const base = all ? names : selected;
                      setSelected(
                        on
                          ? base.filter((entryName) => entryName !== name)
                          : [...base, name],
                      );
                    }}
                  />
                  <span className="mono">{name}</span>
                  {gone && <Badge kind="warn">{t("not served upstream")}</Badge>}
                </label>
                {entry?.description ? (
                  <p className="hint">{entry.description}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {canEdit ? (
        <div className="row" style={{ marginTop: 10 }}>
          <button
            className="primary sm"
            disabled={busy || all}
            onClick={() => save(selected)}
          >
            {busy ? t("Saving…") : t("Save allowlist")}
          </button>
          <span className="hint">
            {t("A tool outside the list is refused with 403, and disappears from tools/list.")}
          </span>
        </div>
      ) : (
        <Notice kind="info">
          {t("Read-only: an administrator of this tenant changes which tools are exposed.")}
        </Notice>
      )}
    </div>
  );
}
