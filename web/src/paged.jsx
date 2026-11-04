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
  // Cursors already walked through to reach the current page. The first page is
  // reached without one, so the stack holds `undefined` at index 0 and grows by
  // one per "next". This is what makes a numbered pager honest on a cursor API:
  // you can always go back to a page you have seen, and forward exactly one
  // step, because that is the only step the server has told you about.
  const [trail, setTrail] = useState([undefined]);
  const [page, setPage] = useState(0);

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
      setItems(page.items);
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
    setTrail([undefined]);
    setPage(0);
    load(undefined);
  }, [key, load]);

  // Step forward one page. The cursor for it is the `next` the server handed
  // back, so the trail grows by exactly one entry and the current index follows.
  const more = useCallback(() => {
    if (!next) return;
    setTrail((stack) => [...stack, next]);
    setPage((index) => index + 1);
    load(next);
  }, [next, load]);

  // Return to a page this client has already walked through. Index 0 is the
  // first page and `trail[n]` is the cursor that *starts* page n. A page further
  // than the trail has never been fetched, so there is nothing to jump to: a
  // numbered pager that invented a cursor would be promising something the
  // server never said.
  const goTo = useCallback(
    (index) => {
      if (index < 0 || index >= trail.length) return;
      setPage(index);
      load(trail[index]);
    },
    [load, trail],
  );

  // After a mutation the newest page is stale; the cursor is not. Re-reading the
  // first page is enough for "the row I just changed", and keeps the list from
  // jumping back to the top of a long scroll it did not lose.
  const reload = useCallback(() => {
    setTrail([undefined]);
    setPage(0);
    load(undefined);
  }, [load]);

  return {
    items,
    next,
    error,
    busy,
    more,
    reload,
    goTo,
    page,
    pages: trail.length,
  };
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

/**
 * The footer of a paged table.
 *
 * The list is cursor-based: there is no offset to ask for and no total to divide,
 * so a "1 2 3 … 12" bar would be a promise the API cannot keep. What *is* true is
 * that this client has walked through a known prefix of the list, and can return
 * to any page in it. So the bar numbers exactly those pages, shows the next one
 * as a step you can take, and says "end of list" when the server stops handing
 * back a cursor.
 *
 * `pages` is how many pages this client has reached (`1` before any "next"),
 * `page` is the zero-based current one, `goTo` returns to one already seen.
 * `shown` is optional — a caller that does not know the count still gets the bar.
 */
export function LoadMore({ next, busy, onClick, shown, pages = 1, page = 0, goTo }) {
  const t = useT();
  if (!next && !shown) return null;
  const last = pages - 1;
  // The window of numbers, so a hundred visited pages do not become a hundred
  // buttons. Two each side of the cursor, matching what a reader expects.
  const numbers = [];
  for (let index = 0; index < pages; index += 1) {
    if (pages > 7 && index > 1 && index < last - 1 && Math.abs(index - page) > 2) {
      if (numbers[numbers.length - 1] !== "gap") numbers.push("gap");
      continue;
    }
    numbers.push(index);
  }
  return (
    <div className="page-bar">
      <span className="page-bar-range">
        {shown ? t("{count} loaded", { count: shown }) : null}
        {next ? null : <span className="muted">{shown ? " · " : ""}{t("end of list")}</span>}
      </span>
      <nav className="page-bar-pages" aria-label={t("Pages")}>
        {goTo ? (
          <button
            className="page-step"
            disabled={busy || page === 0}
            onClick={() => goTo(page - 1)}
            aria-label={t("Previous page")}
          >
            ‹
          </button>
        ) : null}
        {goTo
          ? numbers.map((entry, index) =>
              entry === "gap" ? (
                <span key={`gap-${index}`} className="page-gap" aria-hidden="true">
                  …
                </span>
              ) : (
                <button
                  key={entry}
                  className={entry === page ? "page-step current" : "page-step"}
                  aria-current={entry === page ? "page" : undefined}
                  aria-label={t("Page {number}", { number: entry + 1 })}
                  disabled={busy}
                  onClick={() => goTo(entry)}
                >
                  {entry + 1}
                </button>
              ),
            )
          : null}
        {next ? (
          goTo ? (
            <button
              className="page-step"
              disabled={busy}
              onClick={onClick}
              aria-label={t("Next page")}
            >
              {busy ? "…" : "›"}
            </button>
          ) : (
            <button className="sm" disabled={busy} onClick={onClick}>
              {busy ? t("Loading…") : t("Load more")}
            </button>
          )
        ) : null}
      </nav>
    </div>
  );
}
