// Cursor lists, on the client side of the same contract the API keeps.
//
// One hook and two small components, because five pages ask the same questions:
// which rows do I have, what is the next cursor, and what happens when a filter
// in the header changes. Writing that five times is how they end up disagreeing
// about what "load more" does.

import { useCallback, useEffect, useRef, useState } from "react";

/** How many rows a page holds. The API clamps to 200 either way. */
export const PAGE = 50;

/**
 * A paged list: `items`, `next`, and the filter state that resets it.
 *
 * `fetch` receives `{limit, before, ...filters}` and answers `{items, next}`.
 * A filter change starts from the newest page — a cursor into a list that is no
 * longer the same list is how a page boundary loses rows.
 */
export function usePagedList(fetch, filters = {}) {
  const [items, setItems] = useState(null);
  const [next, setNext] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // The fetch and the filters as refs: `load` must be stable across renders or
  // the effect below re-runs forever, and both change on every keystroke.
  const call = useRef(fetch);
  call.current = fetch;
  const query = useRef(filters);
  query.current = filters;
  const key = JSON.stringify(filters);

  const load = useCallback(async (before) => {
    setBusy(true);
    try {
      const page = await call.current({
        limit: PAGE,
        before,
        ...query.current,
      });
      setItems((current) =>
        before && current ? [...current, ...page.items] : page.items,
      );
      setNext(page.next);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    setItems(null);
    setNext(null);
    load(undefined);
  }, [key, load]);

  const more = useCallback(() => {
    if (next) load(next);
  }, [next, load]);

  // After a mutation the newest page is stale; the cursor is not. Re-reading the
  // first page is enough for "the row I just changed", and keeps the list from
  // jumping back to the top of a long scroll it did not lose.
  const reload = useCallback(() => load(undefined), [load]);

  return { items, next, error, busy, more, reload };
}

/**
 * The filter inputs, in a second header row, one per column.
 *
 * Under the labels rather than in a toolbar: a filter that says "Gateway" is
 * ambiguous until it sits underneath the Gateway column, and one search box
 * across three fields cannot say which of them matched.
 */
export function FilterRow({ fields, value, onChange }) {
  return (
    <tr className="filters">
      {fields.map((field, index) =>
        field ? (
          <th key={field.key}>
            <input
              value={value[field.key] ?? ""}
              placeholder={field.placeholder ?? field.label ?? field.key}
              aria-label={`Filter by ${field.label ?? field.key}`}
              onChange={(event) =>
                onChange({ ...value, [field.key]: event.target.value })
              }
            />
          </th>
        ) : (
          <th key={`spacer-${index}`} />
        ),
      )}
    </tr>
  );
}

/** The one button under a table that has more rows behind it. */
export function LoadMore({ next, busy, onClick }) {
  if (!next) return null;
  return (
    <div className="row" style={{ marginTop: "var(--s3)", justifyContent: "center" }}>
      <button className="cta-secondary" disabled={busy} onClick={onClick}>
        {busy ? "Loading…" : "Load more"}
      </button>
    </div>
  );
}
