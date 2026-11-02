"""Ports: what the application layer needs from the outside world.

Defining these here inverts the dependency — the command handler and the proxy
depend on an interface they own, and Postgres/HTTP live in `infrastructure`. That
is what lets the write path run against a fake log in a unit test, and makes
"swap the storage" a wiring change rather than a rewrite.

Only genuinely replaceable collaborators belong here. The projection runner is
not one: it is the read model's schema, and abstracting it would produce an
interface with a single implementation and no second caller.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from gateway.domain.events import Event


@runtime_checkable
class EventStore(Protocol):
    """The append-only log, as the application uses it.

    `runtime_checkable` so the container (a Pydantic model) can hold one: Pydantic
    validates arbitrary types with `isinstance`, and a plain Protocol refuses that.
    """

    async def load_stream(
        self, aggregate_id: str, up_to: int | None = None
    ) -> list[Event]: ...

    async def append(
        self, events: Sequence[Event], *, expected_version: int, actor: str = "system"
    ) -> list[Event]: ...

    async def append_unversioned(
        self, events: Sequence[Event], *, actor: str = "system"
    ) -> list[Event]: ...

    async def save_checkpoint(self, name: str, global_seq: int) -> None: ...

    async def fetch_operator_by_email(self, email: str) -> str | None: ...

    async def count_operators(self) -> int: ...

    async def resolve_slug(self, slug: str) -> str | None: ...

    async def fetch_tenant_by_code(self, join_code: str) -> dict[str, str] | None: ...

    async def join_code_taken(self, join_code: str) -> bool: ...

    async def recent_events(
        self, *, limit: int = 100, aggregate_ids: Sequence[str] | None = None
    ) -> list[dict]: ...


__all__ = ["EventStore"]
