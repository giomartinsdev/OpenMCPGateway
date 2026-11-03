"""Read-side events: things the log records for the read models only.

The distinction is not cosmetic. A write event changes an aggregate and is folded
back into it; a read event is observed traffic, and folding it into an aggregate
would be meaningless. Keeping the two apart means the write model's vocabulary
cannot drift into telemetry and the projections cannot depend on aggregate
internals.
"""

from pydantic import Field

from gateway.domain.events.base import Event


class RequestObserved(Event):
    """One proxied call, as seen by the gateway.

    Its own stream (`traffic_<gateway_id>`) and appended without a version check:
    observations must never fail each other, and no aggregate ever folds them.
    """

    EVENT = "request.observed"

    gateway_id: str
    subject: str
    outcome: str  # allowed | unauthenticated | forbidden | rate_limited | error
    status: int
    duration_ms: int
    ip: str | None = None
    tool: str | None = None
    method: str = "POST"
    #: Why this was refused, in a sentence a reader can act on. Empty for an
    #: allowed call: the normal case has nothing to explain.
    reason: str = ""
    #: The id the caller sent (or the guard minted). Chosen at the edge, so it
    #: exists before the row does — which is what makes "which call was that" a
    #: question support can ask and this log can answer.
    request_id: str = ""


READ_EVENTS: tuple[type[Event], ...] = (RequestObserved,)

__all__ = ["READ_EVENTS", "Field", "RequestObserved"]
