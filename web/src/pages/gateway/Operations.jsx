// Two things an operator does to a gateway they already own, side by side on the
// detail page: roll the configuration back, and push the traffic log somewhere
// else.
//
// They share a file because they share a rule: neither is a new kind of write.
// A restore appends one event, and a webhook is configuration the log already
// records.

import { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { useT } from "../../i18n.jsx";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../../paged.jsx";
import {
  Badge, Bone, Details, Drawer, Empty, NoMatches, Notice, Skeleton,
  outcomeLabel, relative,
} from "../../ui.jsx";

// One filter per column, matching the audit page's vocabulary: it is the same
// stream, read through a narrower scope.
const HISTORY_FIELDS = [
  null, // version: a number is a sort key, not a search box
  { key: "type", label: "Change" },
  { key: "actor", label: "Actor" },
  null, // when: `since`/`until` are a range, and a range is not a text box
  null, // actions
];

const HISTORY_SORTS = { occurred_at: "desc", actor: "asc", type: "asc" };

/** What a history row looks like before the real one arrives. */
const SAMPLE_HISTORY = [
  { sequence: 3, event_type: "gateway.updated", actor: "someone@example.com", occurred_at: new Date().toISOString(), current: true },
  { sequence: 2, event_type: "gateway.auth_configured", actor: "someone@example.com", occurred_at: new Date().toISOString(), current: false },
  { sequence: 1, event_type: "gateway.created", actor: "someone@example.com", occurred_at: new Date().toISOString(), current: false },
];

/**
 * The gateway's own stream, with the same affordances as every other list in the
 * panel: a cursor, a filter under the columns that take one, sortable headers,
 * and a drawer for "what did version 2 actually hold".
 *
 * Newest first, like `/events`: the change someone is looking for is almost
 * always the one that just happened.
 */
export function HistoryPanel({ gateway, onRestored }) {
  const t = useT();
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("occurred_at", HISTORY_SORTS);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const { items, next, error: listError, busy: loading, more, reload } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.history(gateway.id, { limit, before, ...query });
      return { items: page.history, next: page.next };
    },
    { ...filters, ...sort },
  );

  const restore = useCallback(
    async (version) => {
      setBusy(true);
      setError("");
      try {
        await api.restore(gateway.id, version);
        setPreview(null);
        reload();
        onRestored?.();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [gateway.id, reload, onRestored],
  );

  return (
    <>
      <Notice kind="err">{error || listError}</Notice>
      <Skeleton
        data={items}
        sample={SAMPLE_HISTORY}
        label={t("Loading the history")}
        empty={items?.length === 0}
        emptyMessage={
          <NoMatches
            filters={filters}
            onClear={() => setFilters({})}
            title={t("No history yet")}
          >
            {t("Every change to this gateway lands here.")}
          </NoMatches>
        }
      >
        {(rows) => (
          <div className="table-scroll" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  <th className="num">{t("Version")}</th>
                  <SortHeader field="type" label="Change" sort={sort} onSort={setSort} />
                  <SortHeader field="actor" label="Actor" sort={sort} onSort={setSort} />
                  <SortHeader
                    field="occurred_at"
                    label="When"
                    sort={sort}
                    onSort={setSort}
                    className="num"
                  />
                  <th><span className="sr-only">{t("Operations")}</span></th>
                </tr>
                <FilterRow fields={HISTORY_FIELDS} value={filters} onChange={setFilters} />
              </thead>
              <tbody>
                {rows.map((entry) => (
                  <tr key={entry.sequence}>
                    <td className="num mono">{entry.sequence}</td>
                    <td className="mono">{entry.event_type}</td>
                    <td className="hint mono">{entry.actor || t("system")}</td>
                    <td className="num hint">{relative(entry.occurred_at)}</td>
                    <td className="actions">
                      {/* The current version is what the page already shows, so a
                          "roll back to here" button on it would only ever 422. */}
                      {entry.current ? null : (
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
      </Skeleton>
      <LoadMore next={next} busy={loading} onClick={more} shown={items?.length} />

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
              <th><span className="sr-only">{t("Operations")}</span></th>
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

      <form onSubmit={add} style={{ marginTop: 12 }}>
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
