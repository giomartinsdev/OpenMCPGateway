"""What every aggregate shares: the refusal and the folding base class.

`DomainError` is a command that violated an invariant. It is a plain exception so
the domain can be imported by a test, a CLI or a worker without a framework; the
interface layer maps it to 422.
"""

import re

from pydantic import BaseModel, ConfigDict


class DomainError(ValueError):
    """A command violated an invariant. Surfaced to the caller as HTTP 422."""


class _Aggregate(BaseModel):
    """Mutable state folded from events. Not frozen, not validated on assign.

    Validation happens when an event is constructed, so the state cannot become
    invalid without a bug in a reducer — hence no per-assignment validation,
    which would make folding a stream an order of magnitude slower for nothing.
    """

    model_config = ConfigDict(validate_assignment=False)


SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


__all__ = ["SLUG_RE", "DomainError"]
