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
#: referenced `C` should keep meaning `status`.
COLUMNS: tuple[str, ...] = (
    "occurred_at",
    "gateway",
    "gateway_id",
    "subject",
    "outcome",
    "status",
    "duration_ms",
    "tool",
    "method",
    "ip",
    "reason",
    "event_id",
)

def render(rows: list[dict[str, Any]]) -> str:
    """One CSV document, header first, no BOM.

    `\r\n` because that is what RFC 4180 says and what Excel expects; `csv` owns
    the quoting, so a subject with a comma in it cannot shift a column.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore",
                            lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in COLUMNS})
    return buffer.getvalue()

def _cell(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

__all__ = ["COLUMNS", "render"]
