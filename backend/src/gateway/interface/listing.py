"""Listing: the three query parameters every list shares, and the filter gate.

A dependency rather than three arguments on eleven routes: the clamping rule and
the cursor format then cannot differ per endpoint, and the OpenAPI schema
advertises them once per list.

Filters are read from the raw query string against the endpoint's catalogue, not
declared as keyword arguments. A keyword argument that FastAPI does not know is
silently ignored, and `?outcame=allowed` returning every row is worse than a 422:
the reader believes the filter applied.
"""

from typing import Any

from fastapi import Query, Request

from gateway.domain.filters import UnknownFilter
from gateway.domain.paging import Window


class PageQuery:
    """`limit` + `before`, already clamped and decoded."""

    def __init__(
        self,
        limit: int = Query(50, ge=1, le=200, description="Rows per page"),
        before: str | None = Query(None, description="Cursor of the previous page"),
    ) -> None:
        self.window = Window.parse(limit, before)


def filters(request: Request, catalog: dict[str, type[Any]]) -> dict[str, str]:
    """The raw filter values this request asked for, or a 422 naming the catalog.

    Only names in `catalog` are read: `limit` and `before` are not filters, and
    anything else is refused rather than ignored. Values stay strings — parsing
    to typed clauses is the listing service's job, so a route cannot hand
    `listing` a half-parsed list while another caller hands it a dict.
    """
    known = set(catalog) | {"limit", "before"}
    for name in request.query_params:
        if name not in known:
            raise UnknownFilter(
                f"unknown filter {name!r}; this endpoint filters by: "
                f"{', '.join(sorted(catalog))}"
            )
    return {name: value for name, value in request.query_params.items() if name in catalog}


__all__ = ["PageQuery", "filters"]
