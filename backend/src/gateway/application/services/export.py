"""CSV for the traffic log.

The log is what a SIEM ingests and what a spreadsheet opens, and neither speaks
JSON with a cursor. Same tenant scope, same filters, one file.

Serialized by hand rather than through a spreadsheet library: the columns are a
flat list of scalars, and `csv` is in the standard library.
"""

import csv
import io
from typing import Any

#: Column order, and the header row. Fixed, because a spreadsheet formula that
#: referenced `C` should keep meaning `status`. The byte counts sit next to the
#: latency because a size and a duration are the same kind of question, and the
#: previews are last: they are the only columns that can hold a newline.
COLUMNS: tuple[str, ...] = (
    "occurred_at",
    "gateway",
    "gateway_id",
    "subject",
    "outcome",
    "status",
    "duration_ms",
    "bytes_in",
    "bytes_out",
    "tool",
    "method",
    "ip",
    "reason",
    "event_id",
)

#: The audit log's shape. Separate from `COLUMNS` because an event has no
#: `duration_ms` and a request has no `event_type`; one file that pretends
#: otherwise would ship a column of blanks.
EVENT_COLUMNS: tuple[str, ...] = (
    "occurred_at",
    "event_type",
    "aggregate_id",
    "sequence",
    "actor",
    "event_id",
)

def render(rows: list[dict[str, Any]]) -> str:
    """One CSV document, header first, no BOM.

    `\r\n` because that is what RFC 4180 says and what Excel expects; `csv` owns
    the quoting, so a subject with a comma in it cannot shift a column.
    """
    return _csv(rows, COLUMNS)

def render_events(rows: list[dict[str, Any]]) -> str:
    """The audit log as CSV, tenant scope applied by the caller.

    `payload` is deliberately absent: it holds sealed secrets and key hashes, and
    an export is the most likely file to leave the building.
    """
    return _csv(rows, EVENT_COLUMNS)

def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore",
                            lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return buffer.getvalue()

def _cell(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

__all__ = ["COLUMNS", "EVENT_COLUMNS", "render", "render_events"]
