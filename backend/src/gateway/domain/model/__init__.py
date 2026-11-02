"""Aggregates, one module per stream.

Each is a pure reducer: `from_events` folds the stream, `decide_*` validates a
command against the folded state and returns the events to append. No I/O here —
that is what makes the domain testable without a database and keeps invariants in
one place.

Re-exported from the package so callers say `from gateway.domain.model import
Gateway` and never need to know the file layout.
"""

from gateway.domain.model.base import SLUG_RE, DomainError
from gateway.domain.model.gateway import Gateway
from gateway.domain.model.operator import Operator
from gateway.domain.model.role import Role
from gateway.domain.model.tenant import JOIN_CODE_ALPHABET, Tenant

__all__ = [
    "JOIN_CODE_ALPHABET",
    "SLUG_RE",
    "DomainError",
    "Gateway",
    "Operator",
    "Role",
    "Tenant",
]
