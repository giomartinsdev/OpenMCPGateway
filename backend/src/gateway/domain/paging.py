"""Cursor pagination: one shape for every list, and the rules that keep it honest.

Offset pagination renumbers the page on every write and skips rows when the table
grows underneath the reader; a cursor names a *position* instead.

Convention: rows are ordered by `(sort column, id)`. The `id` is part of the
cursor because two rows with the same value are not an order — a page boundary
between them would duplicate or drop one, and only a tiebreaker makes "next"
exact.

The cursor is opaque on purpose: handing back `at=...&id=...` invites clients to
build their own, and then the encoding is a compatibility promise. It also
carries the sort it was minted under, because a cursor means "everything after
this row *in this order*" and is meaningless under a different one.
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

#: How a sort value travels in the cursor. Without the tag, a role called
#: `2026-11-03T00:00:00` would decode as a datetime and compare against the wrong
#: type.
TIME = "dt"
TEXT = "str"
INT = "int"


class InvalidCursor(ValueError):
    """The cursor is not one this API issued, or not for this ordering."""


@dataclass(frozen=True)
class Sort:
    """One legal order for a list: the name clients use, and the column behind it.

    `desc` is the *default* direction; a request may flip it. Keeping the default
    next to the column means the newest-first lists and the alphabetical ones
    declare their own intent instead of sharing a guess.
    """

    key: str
    column: str
    kind: str = TEXT
    desc: bool = False


@dataclass(frozen=True)
class Cursor:
    """Where a page ended: the sort values of its last row, and the order used.

    `at` is a datetime for a time-ordered list, an int for a numeric one, and a
    string for one ordered by a label.
    """

    at: datetime | str | int
    id: str
    #: Sort key this cursor was minted under. Empty means "unknown", which is only
    #: true of cursors issued before sorting existed; they are accepted as is.
    sort: str = ""
    desc: bool = True

    def encode(self) -> str:
        kind = TIME if isinstance(self.at, datetime) else TEXT
        if isinstance(self.at, int):
            kind = INT
        value = self.at.isoformat() if isinstance(self.at, datetime) else self.at
        raw = json.dumps(
            {
                "k": kind,
                "at": value,
                "id": self.id,
                "s": self.sort,
                "d": 1 if self.desc else 0,
            }
        )
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            body = json.loads(base64.urlsafe_b64decode(padded.encode()))
            at = body["at"]
            kind = body.get("k")
            if kind == TIME:
                at = datetime.fromisoformat(at)
            elif kind == INT:
                at = int(at)
            return cls(
                at=at,
                id=str(body["id"]),
                sort=str(body.get("s") or ""),
                desc=bool(body.get("d", 1)),
            )
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


def same_order(window: Window, sort: Sort) -> None:
    """Refuse a cursor that was minted under a different order.

    Silently reusing it would page through rows compared against the wrong
    column: no error, just a list with holes in it.
    """
    before = window.before
    if before is None or not before.sort:
        return
    if before.sort != sort.key or before.desc != sort.desc:
        direction = "desc" if before.desc else "asc"
        raise InvalidCursor(
            f"cursor was minted for sort={before.sort} {direction}; "
            f"this request sorts by {sort.key} {'desc' if sort.desc else 'asc'}. "
            "Start from the first page when changing the order."
        )


def cut(
    rows: list[dict[str, Any]],
    window: Window,
    sort: Sort,
    *,
    id: str = "id",
) -> tuple[list[dict[str, Any]], str | None]:
    """Trim a page that was fetched with `limit + 1` rows.

    The extra row is the proof that another page exists, so no `count(*)` is
    needed: one row per query instead of a second query.
    """
    if len(rows) <= window.limit:
        return rows, None
    items = rows[: window.limit]
    last = items[-1]
    return items, Cursor(
        at=last[sort.column], id=str(last[id]), sort=sort.key, desc=sort.desc
    ).encode()


__all__ = [
    "DEFAULT_LIMIT",
    "INT",
    "MAX_LIMIT",
    "TEXT",
    "TIME",
    "Cursor",
    "InvalidCursor",
    "Page",
    "Sort",
    "Window",
    "cut",
    "same_order",
]
