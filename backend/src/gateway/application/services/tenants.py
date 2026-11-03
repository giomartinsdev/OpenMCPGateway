"""Tenant commands as a use case: creating one, joining one, reading one.

Creating a tenant grants ownership, joining grants the default role, and reading
one exposes the join code. Those are policy, and policy does not belong in a
route body.
"""

from typing import Any

from gateway.application.container import Container
from gateway.application.principal import Caller
from gateway.console.tokens import mint as mint_console_token
from gateway.domain.errors import NotFound
from gateway.domain.events import AuthMode
from gateway.log import log

#: Preferred slug for the gateway every tenant ships with. Fixed rather than
#: derived from the tenant name: it is the thing a new operator pastes into their
#: client, and "find out what yours is called" is a step nobody should take.
#:
#: Slugs are global, because `/g/<slug>/mcp` is a public URL, so on a deployment
#: with several tenants the first one gets `console` and the next gets
#: `console-2`, `console-3`…
CONSOLE_SLUG = "console"


async def _free_slug(container: Container) -> str:
    candidate, suffix = CONSOLE_SLUG, 2
    while await container.store.resolve_slug(candidate):
        candidate = f"{CONSOLE_SLUG}-{suffix}"
        suffix += 1
    return candidate


async def create(container: Container, caller: Caller, name: str) -> dict[str, Any]:
    """Create a tenant, own it, and give it a way to ask about itself.

    The roles come from the command, not from the caller: the account that
    creates a tenant is its owner by construction, so there is nothing to
    validate against its current permissions (it has none yet).

    The seeded gateway is not a demo: it is this deployment's own console MCP,
    proxied like any other upstream, so the first thing a new tenant can do is
    point an agent at it. Same auth, same rate limiting, same rows in the traffic
    log — a special case here would be a path nobody exercises.
    """
    created = await container.commands.create_tenant(
        name=name, owner_id=caller.operator_id, actor=caller.email
    )
    created["console_gateway"] = await seed_console_gateway(
        container,
        created["tenant_id"],
        owner_id=caller.operator_id,
        email=caller.email,
    )
    return created


async def seed_console_gateway(
    container: Container, tenant_id: str, *, owner_id: str, email: str
) -> dict[str, str]:
    """The `console` gateway: an API key in, the read-only console out.

    The upstream credential is a console token minted for the owner with no
    expiry. It never leaves the deployment — the proxy attaches it — and a token
    that expired after ninety days would turn "works on day one" into a silent
    401 in month four. Callers present an API key like any other gateway, so the
    traffic log stays honest about who asked.

    Failures are logged, not raised: the tenant already exists by now, and
    refusing the whole signup over a convenience gateway trades a real account
    for a broken one. The owner can create one by hand.
    """
    slug = await _free_slug(container)
    try:
        gateway_id = await container.commands.create_gateway(
            tenant_id=tenant_id,
            slug=slug,
            name="Gateway console (read-only)",
            target_url=f"{container.settings.base_url}/mcp",
            description=(
                "This deployment's own read-only MCP: gateways, traffic, audit "
                "log and this manual. Proxied like any other server, so every "
                "question lands in the traffic log."
            ),
            auth_mode=AuthMode.API_KEY,
            actor=email,
        )
        api_key, _ = await container.commands.issue_api_key(
            gateway_id, label="console", actor=email
        )
        await container.commands.configure_auth(
            gateway_id,
            auth_mode=AuthMode.API_KEY,
            upstream_token=mint_console_token(
                secret=container.settings.session_secret.get_secret_value(),
                operator_id=owner_id,
                email=email,
                ttl_seconds=None,
            ),
            actor=email,
        )
    except Exception as exc:
        log.warning(
            "tenant.console_gateway_failed", tenant_id=tenant_id, error=repr(exc)
        )
        return {"slug": CONSOLE_SLUG, "api_key": "", "error": str(exc)}
    await container.refresh_router()
    return {"id": gateway_id, "slug": slug, "api_key": api_key}


async def join(container: Container, caller: Caller, code: str) -> dict[str, Any]:
    """Join with the code the tenant's owner shared."""
    return await container.commands.join_tenant(
        join_code=code, operator_id=caller.operator_id, actor=caller.email
    )


async def current(container: Container, caller: Caller) -> dict[str, Any]:
    """The tenant this account belongs to, including the code to share.

    Readable by every member: the code is what a colleague needs to join, and
    hiding it from everyone but the owner is what makes "ask someone to add you"
    the only path in.
    """
    tenant = await container.read_models.tenant(caller.tenant)
    if tenant is None:
        raise NotFound("tenant not found")
    return {
        "tenant_id": tenant["id"],
        "name": tenant["name"],
        "join_code": tenant["join_code"],
    }


__all__ = ["CONSOLE_SLUG", "create", "current", "join", "seed_console_gateway"]
