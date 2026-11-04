"""Read-model rows: the query side's vocabulary.

These are Pydantic models, not dicts, so a projection and its query cannot drift
apart silently — a renamed column fails validation instead of leaking a `KeyError`
into a dashboard response.

They keep mapping-style access (`row["id"]`) because every consumer already reads
them that way and the JSON shape is the same either way. That compatibility is a
deliberate three-line shim, not a second API.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReadRow(BaseModel):
    """One row of a read model.

    `extra="allow"` because projections add columns before this file knows about
    them; a row must never be rejected for being ahead of its schema.
    """

    model_config = ConfigDict(extra="allow")

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def keys(self) -> list[str]:
        """Enough of the mapping protocol for `{**row}` and `dict(row)`.

        The callers that spread a row are building a per-request copy with extra
        fields (the allowlist), and rewriting them all to `model_dump()` would be
        a bigger diff than this.
        """
        return [*type(self).model_fields, *(self.model_extra or {})]

    def items(self) -> Any:
        return [(key, getattr(self, key)) for key in self.keys()]

    def __iter__(self) -> Any:
        return iter(self.keys())


class GatewayRow(ReadRow):
    id: str
    tenant_id: str
    slug: str
    name: str
    target_url: str
    description: str = ""
    auth_mode: str
    idp: str | None = None
    client_id: str | None = None
    config_url: str | None = None
    required_scopes: list[str] = []
    #: Accept keys as well as the login. Defaults keep pre-existing gateways on
    #: exactly the behaviour they had before this column existed.
    allow_api_key: bool = False
    verifiers: list[dict[str, Any]] = []
    requests_per_minute: int = 0
    #: Upstream tools exposed through this gateway. Empty means all of them.
    tools: list[str] = []
    #: Per credential source overrides: source -> tools. A source with no entry
    #: inherits `tools`.
    tool_scopes: dict[str, list[str]] = {}
    enabled: bool = True
    deleted: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PrincipalRow(ReadRow):
    subject: str
    note: str = ""
    added_at: datetime | None = None


class ApiKeyRow(ReadRow):
    key_id: str
    label: str
    subject: str
    issued_at: datetime | None = None
    #: Whether the plaintext can still be handed back. Never the plaintext
    #: itself: this row is served by a list endpoint to anyone with `keys:read`.
    revealed_at: datetime | None = None
    revealed_by: str | None = None
    #: None means never. The panel warns on a date close to today.
    expires_at: datetime | None = None
    #: "Can this key be revoked?" is a question about evidence. Without a
    #: last-used date the only safe answer is no, so keys outlive the person
    #: who made them. None means "not since the log was pruned".
    last_used_at: datetime | None = None
    recoverable: bool = False

class WebhookRow(ReadRow):
    id: str
    gateway_id: str
    url: str
    #: Never the secret: this row is served by a list endpoint. The secret is
    #: sealed in the database and handed back only once, at creation.
    outcomes: tuple[str, ...] = ()
    enabled: bool = True
    created_at: datetime | None = None

class WebhookDeliveryRow(ReadRow):
    id: str
    webhook_id: str
    event: str
    #: `delivered` or `failed`, after the retries. "The hook is silent" has two
    #: causes — no matching event, or every POST failing — and only a stored
    #: attempt tells them apart.
    status: str
    attempts: int = 0
    status_code: int | None = None
    error: str = ""
    created_at: datetime | None = None

class TenantRow(ReadRow):
    id: str
    name: str
    join_code: str
    created_at: datetime | None = None

class RoleRow(ReadRow):
    id: str
    tenant_id: str
    name: str
    permissions: list[str] = []
    deleted: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

class OperatorRow(ReadRow):
    operator_id: str
    email: str
    tenant_id: str | None = None
    is_admin: bool = False
    role_ids: tuple[str, ...] = ()
    #: Offboarding: a disabled account cannot sign in and its live sessions stop
    #: working on the next request.
    disabled: bool = False
    created_at: datetime | None = None


class RequestRow(ReadRow):
    gateway_id: str
    subject: str
    outcome: str
    status: int
    duration_ms: int
    ip: str | None = None
    tool: str | None = None
    method: str
    #: The id the caller chose (or the guard minted), so a support ticket can be
    #: joined to a row. Empty on rows written before the column existed.
    request_id: str = ""
    occurred_at: datetime


class SeriesPoint(ReadRow):
    bucket: str
    count: int


class TrafficStats(ReadRow):
    window_minutes: int
    requests: int
    #: The same-length window immediately before this one, so a number can be
    #: read as rising or falling instead of on its own.
    previous_requests: int = 0
    avg_duration_ms: float
    max_duration_ms: int
    #: p50/p95/p99 over the retained rows in the window. `avg` hides a tail and
    #: `max` is one unlucky call; a bucket that keeps count/sum/max cannot be
    #: un-aggregated, so these are read from the tail rather than invented.
    p50_duration_ms: float = 0
    p95_duration_ms: float = 0
    p99_duration_ms: float = 0
    by_outcome: dict[str, int]
    by_ip: dict[str, int]
    #: Keyed by gateway slug: the id is not what the panel prints.
    by_gateway: dict[str, int]
    #: Slug -> outcome -> count. The two `by_*` maps above cannot answer "this
    #: gateway is refusing everybody", because the same totals fit a healthy
    #: gateway next to a broken one; the flow chart needs the pair.
    by_gateway_outcome: dict[str, dict[str, int]] = {}
    #: Tool name -> calls in the window. Empty when nothing reached `tools/call`.
    by_tool: dict[str, int] = {}
    series: list[SeriesPoint]
    #: The minute with the most requests, and how many: a spike is easier to
    #: spot in a sentence than in a bar chart.
    busiest: SeriesPoint | None = None


__all__ = [
    "ApiKeyRow",
    "GatewayRow",
    "OperatorRow",
    "PrincipalRow",
    "ReadRow",
    "RequestRow",
    "RoleRow",
    "SeriesPoint",
    "TenantRow",
    "TrafficStats",
    "WebhookDeliveryRow",
    "WebhookRow",
]
