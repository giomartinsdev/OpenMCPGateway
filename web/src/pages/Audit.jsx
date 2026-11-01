import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, Empty, Notice, relative } from "../ui.jsx";

export function Audit() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.events(200).then((data) => setEvents(data.events), (err) => setError(err.message));
  }, []);

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Audit log</h1>
          <div className="sub">
            The append-only event log. Secret values are redacted before they are stored.
          </div>
        </div>
      </div>

      <Notice kind="err">{error}</Notice>

      <Card title={`${events.length} events`}>
        {events.length === 0 ? (
          <Empty>No events yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Type</th>
                <th>Aggregate</th>
                <th>Actor</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.global_seq ?? event.event_id}>
                  <td className="num hint">{event.global_seq}</td>
                  <td className="mono">{event.event_type}</td>
                  <td className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
                    {event.aggregate_id}
                  </td>
                  <td className="hint">{event.actor || "—"}</td>
                  <td className="hint">{relative(event.occurred_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
