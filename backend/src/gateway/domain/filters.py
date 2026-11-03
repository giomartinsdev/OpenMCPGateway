"""Listing vocabulary: what a list may be filtered and sorted by, and how.

Every list endpoint declares two closed sets — its filters and its sort keys —
and the query string is validated against them. Nothing else decides what a
column means to a client:

* An unknown filter is a 422 that names the fields that exist, not a clause that
  is silently ignored — which reads as "no rows matched".
* `contains` for text, `equals` for everything else. Typing "ran" into a gateway
  column means "the name has ran in it"; picking an outcome from a dropdown
  means exactly that outcome.
* Sort keys resolve to columns here, so a client string never reaches
  `.order_by()`.

The vocabulary lives here; the SQL lives in the read model. Public names are kept
apart from column names so a rename does not break a bookmarked URL.
"""

from dataclasses import dataclass, replace
from typing import Any, ClassVar, Literal

from gateway.domain.paging import INT, Sort
from gateway.domain.paging import TIME as TIME_SORT

Op = Literal["eq", "contains", "gte", "lte", "gateway"]

TEXT = "text"
EXACT = "exact"
TIME = "time"
RELATION = "relation"


class UnknownFilter(ValueError):
    """The field is not in this endpoint's catalog."""


class UnknownSort(ValueError):
    """The requested order is not one this endpoint offers."""


@dataclass(frozen=True)
class Filter:
    """One filterable field.

    `column` is the read-model column; `over` widens one box to several columns
    when the reader would expect it to (that is what `columns` is for).

    `match` is declared per field, not derived from `kind`, because `since` and
    `until` are the same kind of value compared two ways.
    """

    name: ClassVar[str]
    column: ClassVar[str]
    kind: ClassVar[str] = EXACT
    match: ClassVar[Op] = "eq"
    #: Set instead of `column` when one input should search several columns.
    over: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def columns(cls) -> tuple[str, ...]:
        """Every column one input searches."""
        return cls.over or (cls.column,)


class Id(Filter):
    """The primary key, matched loosely.

    People read it off a URL or a log line and paste the middle of it, so a
    substring match is what they mean; an exact match would answer "no rows" and
    read as "that row is gone".
    """

    name = "id"
    kind = TEXT
    match = "contains"


class RowId(Id):
    column = "id"


class EventId(Id):
    column = "event_id"


class Gateway(Filter):
    """Matches a gateway by slug or by name: that is what the panel shows.

    People filter by what they read on screen; asking them for the `gw_...` id
    would be an API detail leaking into a search box.
    """

    name = "gateway"
    column = "gateway_id"
    kind = RELATION
    match = "gateway"


class Subject(Filter):
    name = "subject"
    column = "subject"
    kind = TEXT
    match = "contains"


class Outcome(Filter):
    name = "outcome"
    column = "outcome"


class Status(Filter):
    name = "status"
    column = "status"


class Method(Filter):
    name = "method"
    column = "method"


class Tool(Filter):
    name = "tool"
    column = "tool"
    kind = TEXT
    match = "contains"


class Ip(Filter):
    name = "ip"
    column = "ip"
    kind = TEXT
    match = "contains"


class EventType(Filter):
    name = "type"
    column = "event_type"
    kind = TEXT
    match = "contains"


class Aggregate(Filter):
    name = "aggregate"
    column = "aggregate_id"
    kind = TEXT
    match = "contains"


class Actor(Filter):
    name = "actor"
    column = "actor"
    kind = TEXT
    match = "contains"


class Since(Filter):
    name = "since"
    column = "occurred_at"
    kind = TIME
    match = "gte"


class Until(Filter):
    name = "until"
    column = "occurred_at"
    kind = TIME
    match = "lte"


#: Field name -> spec. `GET /requests?unknown=1` is refused against this.
REQUEST_FILTERS: dict[str, type[Filter]] = {
    f.name: f for f in (EventId, Gateway, Subject, Outcome, Status, Method, Tool, Ip)
}
EVENT_FILTERS: dict[str, type[Filter]] = {
    f.name: f for f in (EventId, EventType, Aggregate, Actor, Since, Until)
}


class Slug(Filter):
    name = "slug"
    column = "slug"
    kind = TEXT
    match = "contains"


class Name(Filter):
    name = "name"
    column = "name"
    kind = TEXT
    match = "contains"


class AuthMode(Filter):
    name = "auth_mode"
    column = "auth_mode"


class Enabled(Filter):
    name = "enabled"
    column = "enabled"


class Email(Filter):
    name = "email"
    column = "email"
    kind = TEXT
    match = "contains"


class RoleName(Filter):
    name = "name"
    column = "name"
    kind = TEXT
    match = "contains"


#: Gateways are filtered by their own columns, all of them local text.
GATEWAY_FILTERS: dict[str, type[Filter]] = {
    f.name: f for f in (RowId, Slug, Name, AuthMode, Enabled)
}

#: The address book is scanned by address, never by role.
MEMBER_FILTERS: dict[str, type[Filter]] = {f.name: f for f in (RowId, Email)}

#: Roles too: the label is the name, and there is nothing else to narrow by.
ROLE_FILTERS: dict[str, type[Filter]] = {f.name: f for f in (RowId, RoleName)}


@dataclass(frozen=True)
class Sorts:
    """The orders one list offers, and the one it uses when asked for none.

    Closed for the same reason the filters are: a sort key is client input, and
    `?sort=<column I guessed>` reaching `.order_by()` is an injection with extra
    steps.
    """

    fields: dict[str, Sort]
    default: str

    def resolve(self, key: str | None, order: str | None = None) -> Sort:
        name = key or self.default
        spec = self.fields.get(name)
        if spec is None:
            raise UnknownSort(
                f"unknown sort {name!r}; this endpoint sorts by: "
                f"{', '.join(sorted(self.fields))}"
            )
        direction = (order or "").lower()
        if direction not in {"", "asc", "desc"}:
            raise UnknownSort(f"order must be asc or desc, not {order!r}")
        return replace(spec, desc=spec.desc if not direction else direction == "desc")


GATEWAY_SORTS = Sorts(
    {
        "created_at": Sort("created_at", "created_at", TIME_SORT, desc=True),
        "updated_at": Sort("updated_at", "updated_at", TIME_SORT, desc=True),
        "name": Sort("name", "name"),
        "slug": Sort("slug", "slug"),
    },
    default="created_at",
)
REQUEST_SORTS = Sorts(
    {
        "occurred_at": Sort("occurred_at", "occurred_at", TIME_SORT, desc=True),
        "duration_ms": Sort("duration_ms", "duration_ms", INT, desc=True),
        "status": Sort("status", "status", INT),
        "subject": Sort("subject", "subject"),
    },
    default="occurred_at",
)
EVENT_SORTS = Sorts(
    {
        "occurred_at": Sort("occurred_at", "occurred_at", TIME_SORT, desc=True),
        "actor": Sort("actor", "actor"),
        "type": Sort("type", "event_type"),
    },
    default="occurred_at",
)
MEMBER_SORTS = Sorts(
    {
        "email": Sort("email", "email"),
        "created_at": Sort("created_at", "created_at", TIME_SORT, desc=True),
    },
    default="email",
)
ROLE_SORTS = Sorts(
    {
        "name": Sort("name", "name"),
        "created_at": Sort("created_at", "created_at", TIME_SORT, desc=True),
    },
    default="name",
)


@dataclass(frozen=True)
class Clause:
    columns: tuple[str, ...]
    op: Op
    value: Any


def parse(filters: dict[str, type[Filter]], values: dict[str, Any]) -> list[Clause]:
    """Turn `{field: value}` from the query string into SQL-ready clauses.

    Blank values are dropped instead of matching blank columns: an empty input is
    an empty input, and "only rows with nothing in this column" is never what
    someone typing into a search box meant.
    """
    clauses: list[Clause] = []
    for field, value in values.items():
        if value is None or value == "":
            continue
        spec = filters.get(field)
        if spec is None:
            raise UnknownFilter(
                f"unknown filter {field!r}; this endpoint filters by: "
                f"{', '.join(sorted(filters))}"
            )
        clauses.append(Clause(columns=spec.columns(), op=spec.match, value=value))
    return clauses


__all__ = [
    "EVENT_FILTERS",
    "EVENT_SORTS",
    "GATEWAY_FILTERS",
    "GATEWAY_SORTS",
    "MEMBER_FILTERS",
    "MEMBER_SORTS",
    "REQUEST_FILTERS",
    "REQUEST_SORTS",
    "ROLE_FILTERS",
    "ROLE_SORTS",
    "Clause",
    "Filter",
    "Op",
    "Sorts",
    "UnknownFilter",
    "UnknownSort",
    "parse",
]
