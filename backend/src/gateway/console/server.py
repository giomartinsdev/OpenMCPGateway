"""The read-only console MCP: the deployment, in the shape an agent can ask about.

Read-only is a structural property, not a promise: every tool below calls a
`ReadModels` query or a listing service, and nothing in this file imports
`CommandHandler`. A model that asks the console to delete a gateway has nothing to
call.

Tenant scoping is *also* structural: the caller is resolved from the bearer token
into the same `Caller` the HTTP API uses, and every query takes it. There is no
code path here that reads without one, which is what stops a tool from becoming
the one place that leaks across tenants.

Six tools, because a tool list is a prompt: gateways, one gateway, requests,
events, stats, and the manual itself. Anything else an agent needs is already
reachable from those.
"""

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.auth import TokenVerifier
from fastmcp.server.dependencies import get_http_request

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.application.services import listing, observability
from gateway.console.tokens import BadToken
from gateway.console.tokens import read as read_token
from gateway.docs_content import search as search_docs_content
from gateway.domain.filters import EVENT_FILTERS, parse
from gateway.domain.paging import Window


class ConsoleVerifier(TokenVerifier):
    """Turns a console token into the operator it was minted for.

    The tenant is *not* part of the token: it is read from the read models on
    every request, so a member who is removed from a tenant loses the console on
    their next question instead of ninety days later.
    """

    def __init__(self, container: Container, secret: str) -> None:
        super().__init__(base_url=f"{container.settings.base_url}/mcp")
        self._container = container
        self._secret = secret

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            console = read_token(token, secret=self._secret)
        except BadToken:
            return None
        return AccessToken(
            token=token,
            client_id="console",
            scopes=["console:read"],
            expires_at=console.expires_at,
            subject=console.operator_id,
            claims={"email": console.email},
        )


def build_console(container: Container, secret: str) -> FastMCP:
    """The MCP server, with every tool closing over the container.

    Built per process rather than per request: the tools are static and the
    container is shared. The caller is read inside each tool, from the request the
    transport already authenticated.
    """
    server = FastMCP(
        name="openmcp-gateway-console",
        instructions=(
            "Read-only access to one OpenMCPGateway deployment. Everything is scoped "
            "to the tenant of the token that minted it. Use search_docs to answer "
            "questions about how the gateway works; use the other tools to answer "
            "questions about what this deployment is doing."
        ),
        auth=ConsoleVerifier(container, secret),
    )

    async def caller() -> Caller:
        """Who is asking, resolved fresh — roles and tenant change without a re-mint."""
        request = get_http_request()
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        console = read_token(token, secret=secret)
        tenant = await container.read_models.operator_tenant(console.operator_id)
        return Caller(
            operator_id=console.operator_id,
            email=console.email,
            is_admin=False,
            tenant_id=tenant,
            # The console grants one scope of its own: read. The tenant filter is
            # what makes that safe, and it is applied by every query below.
            permissions=frozenset({"console:read"}),
        )

    @server.tool
    async def search_docs(query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search this project's manual. Use it before guessing how anything works."""
        return search_docs_content(query, limit)

    @server.tool
    async def list_gateways(
        slug: str = "", name: str = "", auth_mode: str = "", limit: int = 50
    ) -> dict[str, Any]:
        """Gateways in this tenant. Filter by `slug`, `name` or `auth_mode`."""
        who = await caller()
        result = await listing.gateways(
            container,
            who,
            window=Window.parse(limit),
            filters={"slug": slug, "name": name, "auth_mode": auth_mode},
        )
        return result.model_dump()

    @server.tool
    async def get_gateway(slug: str) -> dict[str, Any]:
        """One gateway by slug: policy, allowlist, keys, stats and recent calls."""
        from gateway.domain.errors import NotFound

        who = await caller()
        match = await listing.gateways(
            container, who, window=Window.parse(1), filters={"slug": slug}
        )
        if not match.items:
            raise NotFound(f"no gateway with slug {slug!r} in this tenant")
        gateway_id = match.items[0]["id"]
        from gateway.application.services import gateways as gateway_service

        return await gateway_service.detail(container, who, gateway_id)

    @server.tool
    async def search_requests(
        gateway: str = "",
        subject: str = "",
        outcome: str = "",
        tool: str = "",
        status: int | None = None,
        ip: str = "",
        limit: int = 50,
        before: str = "",
    ) -> dict[str, Any]:
        """The request log, newest first. `gateway` matches a slug or name.

        Outcomes are `allowed`, `unauthenticated`, `forbidden`, `rate_limited`,
        `upstream_error`. Pass `before` with the `next` cursor to read further back.
        """
        who = await caller()
        result = await listing.requests(
            container,
            who,
            window=Window.parse(limit, before or None),
            filters={
                "gateway": gateway,
                "subject": subject,
                "outcome": outcome,
                "tool": tool,
                "status": status,
                "ip": ip,
            },
        )
        return result.model_dump()

    @server.tool
    async def search_events(
        type: str = "",
        aggregate: str = "",
        actor: str = "",
        since: str = "",
        until: str = "",
        limit: int = 50,
        before: str = "",
    ) -> dict[str, Any]:
        """The audit log, newest first. `type` is a substring of the event type.

        `since` / `until` are ISO-8601 timestamps. Payloads are already redacted.
        """

        who = await caller()
        # Parsed here rather than in `page_events`: the audit read is on the store,
        # not the listing service, so this is the one caller that must build
        # clauses itself.
        clauses = parse(
            EVENT_FILTERS,
            {
                "type": type,
                "aggregate": aggregate,
                "actor": actor,
                "since": _when(since),
                "until": _when(until),
            },
        )
        rows, next_cursor = await container.store.page_events(
            Window.parse(limit, before or None),
            aggregate_ids=await observability.owned_aggregates(container, who),
            clauses=clauses,
        )
        return {"items": rows, "next": next_cursor}

    @server.tool
    async def traffic_stats(
        gateway: str = "", minutes: int = 60
    ) -> dict[str, Any]:
        """Totals, per-outcome, per-IP and per-gateway counts for the last N minutes."""
        who = await caller()
        gateway_id = None
        if gateway:
            match = await listing.gateways(
                container, who, window=Window.parse(1), filters={"slug": gateway}
            )
            if not match.items:
                return {"error": f"no gateway with slug {gateway!r} in this tenant"}
            gateway_id = match.items[0]["id"]
        stats = await observability.stats(container, who, gateway_id, minutes)
        return stats.model_dump()

    return server


def _when(value: str) -> Any:
    """Parse an optional ISO timestamp, or drop the filter."""
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone()
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc


__all__ = ["ConsoleVerifier", "build_console"]
