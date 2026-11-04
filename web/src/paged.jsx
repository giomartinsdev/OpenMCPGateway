// Cursor lists, on the client side of the same contract the API keeps.
//
// One hook and three small components, because five pages ask the same
// questions: which rows do I have, what is the next cursor, and what happens
// when a filter in the header changes. Writing that five times is how they end
// up disagreeing about what "load more" does.

import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "./i18n.jsx";

/** How many rows a page holds. The API clamps to 200 either way. */
export const PAGE = 50;

/**
 * How long a keystroke waits before it becomes a request.
 *
 * Filtering on every character makes the list flicker through half-typed words:
 * typing "cli" fires "c", "cl", "cli" and the first two answer "nothing
 * matched". Three hundred milliseconds is under the pause between two words and
 * over the gap between two keys.
 */
export const DEBOUNCE_MS = 300;

/**
 * A paged list: `items`, `next`, and the query state that resets it.
 *
 * `fetch` receives `{limit, before, ...query}` and answers `{items, next}`.
 * A query change starts from the newest page — a cursor into a list that is no
 * longer the same list is how a page boundary loses rows.
 *
 * `items` is deliberately *not* cleared while a new page loads: the table's
 * header holds the filter inputs, so unmounting it would steal focus from the
 * box someone is typing in.
 */
export function usePagedList(fetch, query = {}) {
  const [items, setItems] = useState(null);
  const [next, setNext] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // The fetch and the query as refs: `load` must be stable across renders or
  // the effect below re-runs forever, and both change on every keystroke.
  const call = useRef(fetch);
  call.current = fetch;
  const current = useRef(query);
  current.current = query;
  const key = JSON.stringify(query);

  const load = useCallback(async (before) => {
    setBusy(true);
    try {
      const page = await call.current({
        limit: PAGE,
        before,
        ...current.current,
      });
      setItems((previous) =>
        before && previous ? [...previous, ...page.items] : page.items,
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
 *
 * Each box clears on Escape and carries a `x` while it has text: emptying a
 * filter is how you undo it, and a box you cannot empty is a box you retype the
 * URL to escape.
 */
export function FilterRow({ fields, value, onChange }) {
  const t = useT();
  // Draft is local so typing stays instant; the debounce decides when the list
  // hears about it.
  const [draft, setDraft] = useState(value);
  const timer = useRef(null);
  const settle = useRef(onChange);
  settle.current = onChange;

  // A filter reset from outside (a page reload, a reset button) must win over a
  // keystroke that is still waiting out its debounce.
  const incoming = JSON.stringify(value);
  useEffect(() => {
    setDraft(JSON.parse(incoming));
  }, [incoming]);

  useEffect(() => () => clearTimeout(timer.current), []);

  function edit(key, text) {
    const next = { ...draft, [key]: text };
    setDraft(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => settle.current(next), DEBOUNCE_MS);
  }

  return (
    <tr className="filters">
      {fields.map((field, index) =>
        field ? (
          <th key={field.key}>
            <span className="filter-box">
              <input
                value={draft[field.key] ?? ""}
                placeholder={t(field.placeholder ?? field.label ?? field.key)}
                aria-label={t("Filter by {field}", { field: t(field.label ?? field.key) })}
                onChange={(event) => edit(field.key, event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Escape") return;
                  event.preventDefault();
                  edit(field.key, "");
                }}
              />
              {draft[field.key] ? (
                <button
                  type="button"
                  className="clear-filter"
                  title={t("Clear")}
                  aria-label={t("Clear {field} filter", {
                    field: t(field.label ?? field.key),
                  })}
                  onClick={() => edit(field.key, "")}
                >
                  ×
                </button>
              ) : null}
            </span>
          </th>
        ) : (
          // A column with no filter still needs a cell here, but an empty `th`
          // has no accessible name and is announced as a header with nothing to
          // say. `presentation` says "this cell is layout, not a label".
          <th key={`spacer-${index}`} role="presentation" />
        ),
      )}
    </tr>
  );
}

/**
 * A column header that asks the server to reorder the list.
 *
 * The first click on a column uses its natural direction (newest first for
 * timestamps, A–Z for names) and the second flips it: guessing the direction a
 * reader wants is one decision too many for a click they will make again anyway.
 */
export function SortHeader({ field, label, sort, onSort, sortable = true, ...rest }) {
  const t = useT();
  if (!sortable) return <th {...rest}>{t(label)}</th>;
  const active = sort.sort === field;
  const arrow = !active ? "" : sort.order === "asc" ? "↑" : "↓";
  return (
    <th
      {...rest}
      aria-sort={
        active ? (sort.order === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        className={active ? "sort active" : "sort"}
        onClick={() => onSort(field)}
      >
        {t(label)}
        <span aria-hidden="true">{arrow}</span>
      </button>
    </th>
  );
}

/**
 * The sort state a page keeps, as the exact pair of query parameters it sends.
 *
 * `defaults` mirrors the server's own directions, so the first click on a column
 * does what its data suggests: newest first for a timestamp, A–Z for a label.
 * Clicking the active column flips it.
 *
 * The returned value goes straight into the fetch query, so it holds nothing the
 * API would reject as an unknown parameter.
 */
export function useSort(defaultSort, defaults = {}) {
  const [sort, setSort] = useState({
    sort: defaultSort,
    order: defaults[defaultSort] ?? "asc",
  });
  const first = useRef(defaults);
  first.current = defaults;
  const change = useCallback((field) => {
    setSort((current) =>
      current.sort === field
        ? { sort: field, order: current.order === "asc" ? "desc" : "asc" }
        : { sort: field, order: first.current[field] ?? "asc" },
    );
  }, []);
  return [sort, change];
}

/** The one button under a table that has more rows behind it. */
export function LoadMore({ next, busy, onClick }) {
  const t = useT();
  if (!next) return null;
  return (
    <div className="row" style={{ marginTop: "var(--s3)", justifyContent: "center" }}>
      <button className="cta-secondary" disabled={busy} onClick={onClick}>
        {busy ? t("Loading…") : t("Load more")}
      </button>
    </div>
  );
}
