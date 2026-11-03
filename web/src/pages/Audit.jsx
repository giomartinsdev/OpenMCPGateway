import { useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, SortHeader, usePagedList, useSort } from "../paged.jsx";
import {
  Badge, Card, Empty, Notice, Skeleton, SAMPLE_EVENTS, relative,
} from "../ui.jsx";

// The filter fields are the table's own labels, in the table's own order, which
// is why this is a list of objects and not a toolbar of inputs.
const FIELDS = [
  null, // the sequence number is not a filter
  { key: "type", label: "Type" },
  { key: "aggregate", label: "Aggregate" },
  { key: "actor", label: "Actor" },
  null, // when: `since`/`until` are a range, and a range is not a text box
  { key: "id", label: "ID" },
];

const SORTS = { occurred_at: "desc", actor: "asc", type: "asc" };

export function Audit() {
  const [filters, setFilters] = useState({});
  const [sort, setSort] = useSort("occurred_at", SORTS);
  const { items, next, error, busy, more } = usePagedList(
    async ({ limit, before, ...query }) => {
      const page = await api.events({ limit, before, ...query });
      return { items: page.events, next: page.next };
    },
    { ...filters, ...sort },
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <div className="sub">
            The append-only event log. Secret values are redacted before they are stored.
          </div>
        </div>
        <div className="actions">
          <Badge kind="info">{items ? `${items.length} events` : "loading"}</Badge>
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      <Card title="Events">
        <Skeleton
          data={items}
          sample={SAMPLE_EVENTS}
          label="Loading the audit log"
          empty={items?.length === 0}
          emptyMessage={<Empty title="No events yet">Every change lands here.</Empty>}
        >
          {(rows) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <SortHeader
                      field="type"
                      label="Type"
                      sort={sort}
                      onSort={setSort}
                    />
                    <th>Aggregate</th>
                    <SortHeader
                      field="actor"
                      label="Actor"
                      sort={sort}
                      onSort={setSort}
                    />
                    <SortHeader
                      field="occurred_at"
                      label="When"
                      sort={sort}
                      onSort={setSort}
                      className="num"
                    />
                    <th>ID</th>
                  </tr>
                  <FilterRow fields={FIELDS} value={filters} onChange={setFilters} />
                </thead>
                <tbody>
                  {rows.map((event) => (
                    <tr key={event.event_id}>
                      <td className="num hint">{event.global_seq}</td>
                      <td className="mono strong">{event.event_type}</td>
                      <td className="mono hint">{event.aggregate_id}</td>
                      <td className="hint">{event.actor || "—"}</td>
                      <td className="num hint nowrap">{relative(event.occurred_at)}</td>
                      <td className="mono hint">{event.event_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Skeleton>
        <LoadMore next={next} busy={busy} onClick={more} />
      </Card>
    </>
  );
}
