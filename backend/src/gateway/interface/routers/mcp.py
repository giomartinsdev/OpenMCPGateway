"""The manual and the console token: the only two things an MCP client needs.

`GET /api/docs` is public because the manual is what someone reads before signing
in, and because it is already on the landing page. `POST /api/mcp/token` is not:
minting a credential is exactly the kind of thing that needs a session.
"""

from typing import Any

from fastapi import APIRouter

from gateway.console.tokens import TOKEN_TTL_SECONDS, mint
from gateway.docs_content import sections
from gateway.interface.dependencies import ContainerDep, PrincipalDep

router = APIRouter()


@router.get("/docs")
async def docs() -> dict[str, Any]:
    """The manual, as data. Same text the panel renders and the console searches."""
    return {"sections": list(sections())}


@router.post("/mcp/token", status_code=201)
async def mint_console_token(
    principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    """Mint a read-only console token for the signed-in operator.

    Returned in plaintext exactly once, like an API key: nothing stores it, so
    there is nothing to leak later. It carries no tenant or permission — both are
    resolved per request, so a role change takes effect immediately.
    """
    if not principal.onboarded:
        # A console over nothing is a console that answers 403 to every question.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403, detail="onboarding: create or join a tenant first"
        )
    return {
        "operator_id": principal.session.operator_id,
        "token": mint(
            secret=container.settings.session_secret.get_secret_value(),
            operator_id=principal.session.operator_id,
            email=principal.session.email,
        ),
        "expires_in": TOKEN_TTL_SECONDS,
        "endpoint": f"{container.settings.base_url}/mcp",
        "tools": [
            "search_docs",
            "list_gateways",
            "get_gateway",
            "search_requests",
            "search_events",
            "traffic_stats",
        ],
    }


__all__ = ["router"]
