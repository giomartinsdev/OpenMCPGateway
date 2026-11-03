import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Empty, Notice, Skeleton, SAMPLE_EVENTS, relative } from "../ui.jsx";

export function Audit() {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.events(200).then((data) => setEvents(data.events), (err) => setError(err.message));
  }, []);

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
          <Badge kind="info">{events ? `${events.length} events` : "loading"}</Badge>
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      <Card title="Events">
        <Skeleton
          data={events}
          sample={SAMPLE_EVENTS}
          label="Loading the audit log"
          empty={events?.length === 0}
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
                </thead>
                <tbody>
                  {rows.map((event) => (
                    <tr key={event.global_seq ?? event.event_id}>
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
      </Card>
    </>
  );
}
