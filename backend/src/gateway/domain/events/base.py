"""The event envelope: what every event carries regardless of what happened.

An event is a Pydantic model rather than a dataclass because the same class is
used for three jobs, and two of them involve untrusted input:

* deciding (built in-process, validated at construction)
* persisting (`payload()` must be JSON)
* rehydrating (`model_validate(row)`) — this is where Pydantic earns its keep:
  JSON has no enums, no tuples and no datetimes, and hand-written coercion is
  exactly the silent-mistake factory this replaces

`EVENT` is a `ClassVar` on purpose: it is the discriminator in the stored row and
must not become part of the payload.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class AuthMode(StrEnum):
    """How a caller proves identity to a gateway route."""

    NONE = "none"  # public route — no credentials required
    API_KEY = "api_key"  # static bearer/header key issued by the gateway
    OAUTH = "oauth"  # OAuth proxy to an upstream IdP (github, google, azure, oidc)


class IdentityProvider(StrEnum):
    """Upstream identity providers the OAuth proxy can delegate to."""

    GITHUB = "github"
    GOOGLE = "google"
    AZURE = "azure"
    DISCORD = "discord"
    OIDC = "oidc"  # any discovery-document-compliant issuer

class VerifierSpec(BaseModel):
    """A second authorization server whose tokens this gateway accepts.

    Not a login flow: the client already holds a token from its own issuer, and
    the gateway only checks the signature, the issuer and the audience. `label`
    names the source in logs and must be unique, because "which provider
    rejected this" is the first question after a 401.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    #: Expected `iss`. Exact match: a verifier that ignored it would accept any
    #: token signed by a key it happens to trust.
    issuer: str
    #: Expected `aud`, when the issuer sets one. Empty means "do not check".
    audience: str = ""
    #: Where the signing keys live. Fetched and cached by the verifier.
    jwks_uri: str


def _now() -> datetime:
    return datetime.now(UTC)


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


#: Fields that belong to the envelope, not to any individual event's payload.
ENVELOPE = frozenset({"aggregate_id", "sequence", "event_id", "occurred_at", "actor"})


class Event(BaseModel):
    """Base event. `aggregate_id` + `sequence` locate it in a stream.

    Frozen: an event that already happened cannot change. A "fix" is a new event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    EVENT: ClassVar[str] = ""

    aggregate_id: str
    sequence: int = 0
    event_id: str = Field(default_factory=_new_event_id)
    occurred_at: datetime = Field(default_factory=_now)
    actor: str = "system"

    def payload(self) -> dict[str, Any]:
        """Serializable body — the fields beyond the envelope.

        `mode="json"` is what turns enums into their values, tuples into lists and
        datetimes into ISO strings, so no per-event `payload()` is needed.
        """
        return {
            key: value
            for key, value in self.model_dump(mode="json").items()
            if key not in ENVELOPE
        }

    def as_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "event_type": self.EVENT,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "payload": self.payload(),
        }


__all__ = [
    "ENVELOPE",
    "AuthMode",
    "Event",
    "IdentityProvider",
    "StrEnum",
]
