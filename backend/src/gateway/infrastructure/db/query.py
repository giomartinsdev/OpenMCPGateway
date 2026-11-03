"""Turning filter clauses and a cursor into SQLAlchemy.

Every list pages the same way — newest first by `(time, id)` with an opaque
cursor — and filters the same way — a closed catalogue of columns. Doing it once
here means a new list endpoint is a `select()` plus a call, and the tiebreaker
cannot be forgotten on one of them.

Nothing from the client reaches this module as SQL: column names come from the
domain catalogue, values arrive as bind parameters.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.sql import Select

from gateway.domain.filters import Clause
from gateway.domain.paging import Window
from gateway.infrastructure.db.tables import rm_gateways


def predicates(table: Any, items: Iterable[Clause]) -> list[ColumnElement[bool]]:
    """One SQL predicate per filter clause, against `table`'s columns."""
    result: list[ColumnElement[bool]] = []
    for clause in items:
        column = table.c[clause.column]
        value = _coerced(column, clause.value)
        match clause.op:
            case "contains":
                result.append(column.ilike(f"%{value}%"))
            case "gte":
                result.append(column >= value)
            case "lte":
                result.append(column <= value)
            case "gateway":
                result.append(column.in_(_gateway_ids(value)))
            case _:
                result.append(column == value)
    return result


def _coerced(column: Any, value: Any) -> Any:
    """A query string is text; Postgres will not compare text to an integer.

    `?status=403` arrives as `"403"` and would be `integer = varchar` at the
    server. The column already knows its type, so the cast belongs here rather
    than in every client that types a number into a filter box. A value that does
    not parse passes through unchanged and simply matches nothing.
    """
    if isinstance(value, str):
        try:
            kind = column.type.python_type
        except NotImplementedError:
            return value
        try:
            if kind is bool:
                return value.lower() in {"1", "true", "yes"}
            if kind is int:
                return int(value)
            if kind is float:
                return float(value)
        except ValueError:
            return value
    return value


def _gateway_ids(text: Any) -> Any:
    """Ids whose slug or name contains `text` — a subquery, not a join.

    The caller filters by what the panel shows it; the row keeps its own
    `gateway_id`, so nothing downstream has to know a join happened.
    """
    pattern = f"%{text}%"
    return select(rm_gateways.c.id).where(
        or_(rm_gateways.c.slug.ilike(pattern), rm_gateways.c.name.ilike(pattern))
    )


def paged(
    query: Select[Any],
    table: Any,
    window: Window,
    *,
    at: str,
    id: str,
) -> Select[Any]:
    """A cursor, the tiebreaker, and one row more than the page.

    The extra row is how "is there a next page" is answered without a
    `count(*)`: `cut()` drops it and reads the cursor off the last row kept.
    """
    if window.before is not None:
        query = query.where(
            (table.c[at] < window.before.at)
            | ((table.c[at] == window.before.at) & (table.c[id] < window.before.id))
        )
    return query.order_by(table.c[at].desc(), table.c[id].desc()).limit(
        window.limit + 1
    )


__all__ = ["paged", "predicates"]
