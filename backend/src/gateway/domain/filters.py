"""Filtering for list endpoints: what may be filtered, and how a value compares.

Two rules, both learned from the alternative:

* **A closed set per endpoint.** A filter name is data from the client, so an
  unknown field is a 422 that names the fields that exist — not a clause that is
  silently ignored, which reads as "no rows matched".
* **`contains` for text, `equals` for everything else.** Typing "ran" into a
  gateway column means "the gateway name has ran in it"; picking an outcome from
  a dropdown means exactly that outcome. Guessing per value would make
  `outcome=allowed` also match `allowed-ish`.

The vocabulary lives here; the SQL lives in the read model. Public names are kept
apart from column names so a rename does not break a bookmarked URL.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

Op = Literal["eq", "contains", "gte", "lte", "gateway"]

TEXT = "text"
EXACT = "exact"
TIME = "time"
RELATION = "relation"


class UnknownFilter(ValueError):
    """The field is not in this endpoint's catalog."""


@dataclass(frozen=True)
class Filter:
    """One filterable field.

    `column` is the read-model column; `related` marks a filter that matches a
    *slug or name* instead, resolved to an id by the read model (people filter by
    the name they see, not by an opaque id they never read).
    """

    name: ClassVar[str]
    column: ClassVar[str]
    kind: ClassVar[str] = EXACT
    #: How this field is compared. Declared per field, not derived from `kind`,
    #: because `since` and `until` are the same kind of value compared two ways.
    match: ClassVar[Op] = "eq"


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
    f.name: f for f in (Gateway, Subject, Outcome, Status, Method, Tool, Ip)
}
EVENT_FILTERS: dict[str, type[Filter]] = {
    f.name: f for f in (EventType, Aggregate, Actor, Since, Until)
}

#: Gateways are filtered by their own text columns.


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


#: Gateways are filtered by their own columns, all of them local text.
GATEWAY_FILTERS: dict[str, type[Filter]] = {
    f.name: f for f in (Slug, Name, AuthMode, Enabled)
}

#: The address book is scanned by name, never by id.
MEMBER_FILTERS: dict[str, type[Filter]] = {f.name: f for f in (Email,)}

#: Roles too: the label is the name, and there is nothing else to narrow by.
ROLE_FILTERS: dict[str, type[Filter]] = {f.name: f for f in (Name,)}


@dataclass(frozen=True)
class Clause:
    column: str
    op: Op
    value: Any


def parse(
    filters: dict[str, type[Filter]], values: dict[str, Any]
) -> list[Clause]:
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
        clauses.append(Clause(column=spec.column, op=spec.match, value=value))
    return clauses


__all__ = [
    "EVENT_FILTERS",
    "GATEWAY_FILTERS",
    "MEMBER_FILTERS",
    "REQUEST_FILTERS",
    "ROLE_FILTERS",
    "Clause",
    "Filter",
    "Op",
    "UnknownFilter",
    "parse",
]
