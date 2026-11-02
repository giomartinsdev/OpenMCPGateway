"""Projections: fold the event log into query-optimised read models.

Split by concern — the handlers that write each row, the runner that dispatches
to them, and the queries the app reads back — and re-exported here so the wiring
does not care about the layout.
"""

from gateway.infrastructure.eventstore.projections.readmodels import ReadModels
from gateway.infrastructure.eventstore.projections.runner import ProjectionRunner

__all__ = ["ProjectionRunner", "ReadModels"]
