"""Domain refusals that are not about a single aggregate.

`DomainError` is "you asked for something the aggregate cannot do". These are
the same kind of refusal one level up — the thing you asked for is not there, or
you are not allowed to look — and they exist so a use case can say so without
importing `fastapi` to do it.
"""


class DomainRefusal(Exception):
    """A request the domain refuses. Translated to a 4xx at the edge."""


class NotFound(DomainRefusal):
    """The thing exists conceptually but not for this caller."""
