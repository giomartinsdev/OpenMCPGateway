"""Cursor pagination: one shape for every list, and the rules that keep it honest.

Offset pagination renumbers the page on every write and skips rows when the table
grows underneath the reader; a cursor names a *position* instead.

Convention: rows are ordered by `(at, id)`. The `id` is part of the cursor because
two requests in the same millisecond are not an order — a page boundary between
them would duplicate or drop one, and only a tiebreaker makes "next" exact.

The cursor is opaque on purpose: handing back `at=...&id=...` invites clients to
build their own, and then the encoding is a compatibility promise.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

#: Hard ceiling for one page. A bigger `limit` is clamped, not refused: someone
#: asking for everything gets the maximum, which is the same intent.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class InvalidCursor(ValueError):
    """The cursor is not one this API issued."""


@dataclass(frozen=True)
class Cursor:
    """Where a page ended: the sort values of its last row.

    `at` is a datetime for a time-ordered list (traffic, audit, gateways) and a
    string for one ordered by a label (roles by name). The kind travels with the
    value so decoding never has to guess: a role called `2026-11-03T00:00:00`
    would otherwise come back as a timestamp and compare against the wrong type.
    """

    at: datetime | str
    id: str

    def encode(self) -> str:
        kind = "dt" if isinstance(self.at, datetime) else "str"
        value = self.at.isoformat() if isinstance(self.at, datetime) else str(self.at)
        raw = json.dumps({"k": kind, "at": value, "id": self.id})
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            body = json.loads(base64.urlsafe_b64decode(padded.encode()))
            at = body["at"]
            if body.get("k") == "dt":
                at = datetime.fromisoformat(at)
            return cls(at=at, id=str(body["id"]))
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise InvalidCursor(f"not a cursor: {value!r}") from exc


class Page(BaseModel):
    """A slice of a list, plus what a client needs to ask for more.

    `items` is `list[Any]` because every endpoint pages a different row type and
    each of those is already a Pydantic model; a generic here would buy nothing
    the endpoint's own annotation does not.
    """

    items: list[Any]
    #: Cursor for the older page. `None` means this page reached the end.
    next: str | None = None


@dataclass(frozen=True)
class Window:
    """A page request, clamped and parsed."""

    limit: int
    before: Cursor | None = None

    @classmethod
    def parse(cls, limit: int | None = None, before: str | None = None) -> Window:
        return cls(
            limit=max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT)),
            before=Cursor.decode(before) if before else None,
        )


def cut(
    rows: list[dict[str, Any]],
    window: Window,
    *,
    at: str = "occurred_at",
    id: str = "event_id",
) -> tuple[list[dict[str, Any]], str | None]:
    """Trim a page that was fetched with `limit + 1` rows.

    The extra row is the proof that another page exists, so no `count(*)` is
    needed: one row per query instead of a second query.
    """
    if len(rows) <= window.limit:
        return rows, None
    items = rows[: window.limit]
    last = items[-1]
    return items, Cursor(at=last[at], id=str(last[id])).encode()


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Cursor",
    "InvalidCursor",
    "Page",
    "Window",
    "cut",
]
