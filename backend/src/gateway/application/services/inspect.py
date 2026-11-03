"""History and rollback: what a gateway's own stream says, and going back.

Two things an operator does to a gateway they already own, neither of which is a
new kind of write: read its stream, and re-apply an older configuration.
"""

from typing import Any

from gateway.application.container import Container
from gateway.domain.filters import EVENT_FILTERS, EVENT_SORTS, parse
from gateway.domain.model import Gateway
from gateway.domain.paging import Page, Window, same_order

#: Fields a restore deliberately does not carry. Named here so a reader can see
#: the boundary rather than infer it from what is missing in the event.
_NOT_RESTORED = frozenset({"client_secret", "upstream_token", "api_keys", "enabled"})


async def history(
    container: Container, gateway: dict[str, Any], *,
    window: Window, filters: dict[str, Any],
    sort: str | None = None, order: str | None = None,
) -> Page:
    """One gateway's stream, newest first, paged and filtered like the audit log.

    Same vocabulary as `/events` because it is the same kind of question asked at
    a narrower scope: "who changed this target_url, and when" is a filter over one
    aggregate, not a needle in the tenant's feed. The aggregate is fixed here, so
    a caller cannot widen the scope with a query string.
    """
    order_by = EVENT_SORTS.resolve(sort, order)
    same_order(window, order_by)
    rows, next_cursor = await container.store.page_events(
        window,
        aggregate_ids=[gateway["id"]],
        clauses=parse(EVENT_FILTERS, filters),
        sort=order_by,
    )
    # The panel hides "restore" on the version the gateway is already at, and a
    # preview of it would only ever 422. Reading the stream for its length is
    # cheaper than a new port method, and a stream is tens of events.
    # ponytail: whole-stream read per page; use a max(sequence) query if a stream
    # ever grows past a few hundred events.
    stream = await container.store.load_stream(gateway["id"])
    current = stream[-1].sequence if stream else 0
    return Page(
        items=[
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "occurred_at": row["occurred_at"],
                "current": row["sequence"] == current,
                # Payloads can hold hashes/secrets — redact before serving.
                "changes": _redact(row["payload"]),
            }
            for row in rows
        ],
        next=next_cursor,
    )


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Never hand a secret back through a read endpoint.

    The event is history and keeps the value; the panel only needs to know that
    the field changed. A hash would still be a fingerprint of a live credential,
    so the key is kept and the value replaced with a marker.
    """
    return {
        key: ("***" if key in _NOT_RESTORED or "secret" in key or "token" in key
              or key in {"key_hash", "sealed"} else value)
        for key, value in payload.items()
    }


async def configuration_at(
    container: Container, gateway: dict[str, Any], version: int
) -> dict[str, Any]:
    """What the gateway looked like after `version`, for the panel's preview."""
    events = await container.store.load_stream(gateway["id"])
    return _snapshot(Gateway.from_events(gateway["id"], [
        event for event in events if event.sequence <= version
    ]))


def _snapshot(gateway: Gateway) -> dict[str, Any]:
    return {
        "version": gateway.version,
        "name": gateway.name,
        "target_url": gateway.target_url,
        "description": gateway.description,
        "requests_per_minute": gateway.requests_per_minute,
        "monthly_quota": gateway.monthly_quota,
        "tools": list(gateway.tools),
        "auth_mode": gateway.auth_mode.value,
        "enabled": gateway.enabled,
    }


__all__ = ["configuration_at", "history"]
