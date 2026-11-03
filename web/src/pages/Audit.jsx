import { useState } from "react";
import { api } from "../api.js";
import { FilterRow, LoadMore, usePagedList } from "../paged.jsx";
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
  null,
];

export function Audit() {
  const [filters, setFilters] = useState({});
  const { items, next, error, busy, more } = usePagedList(
    async ({ limit, before, ...fields }) => {
      const page = await api.events({ limit, before, ...fields });
      return { items: page.events, next: page.next };
    },
    filters,
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
                    <th>Type</th>
                    <th>Aggregate</th>
                    <th>Actor</th>
                    <th className="num">When</th>
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
