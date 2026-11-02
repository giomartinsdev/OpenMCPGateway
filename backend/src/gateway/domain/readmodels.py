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
    requests_per_minute: int = 0
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
    occurred_at: datetime


class SeriesPoint(ReadRow):
    bucket: str
    count: int


class TrafficStats(ReadRow):
    window_minutes: int
    requests: int
    avg_duration_ms: float
    max_duration_ms: int
    by_outcome: dict[str, int]
    by_ip: dict[str, int]
    by_gateway: dict[str, int]
    series: list[SeriesPoint]


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
]
