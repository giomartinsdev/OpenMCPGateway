"""Building the FastMCP OAuth provider for one gateway, per identity provider."""

from typing import Any

from fastmcp.server.auth import AuthProvider

from gateway.domain.events import AuthMode, IdentityProvider
from gateway.proxy.identity import GatewayNotReady


def build_auth_provider(
    config: dict[str, Any], *, base_url: str, credentials: dict[str, str]
) -> AuthProvider | None:
    """Instantiate the OAuth proxy for a gateway, or None for non-OAuth modes.

    Only OAuth modes get a provider: API keys are verified by the gateway itself
    and need no discovery document, and public routes need no auth at all.

    Raises `GatewayNotReady` when an OAuth gateway has no credentials yet. That is
    a normal intermediate state — you create a gateway, then paste the client id —
    and it must fail *closed*: falling back to "no provider" would silently turn
    an OAuth route into an unauthenticated one.
    """
    mode = AuthMode(config["auth_mode"])
    if mode is not AuthMode.OAUTH:
        return None

    idp = IdentityProvider(config["idp"])
    if not credentials.get("client_id"):
        raise GatewayNotReady("OAuth is selected but no client_id is configured")
    if idp is IdentityProvider.OIDC and not credentials.get("config_url"):
        raise GatewayNotReady("OIDC is selected but no discovery URL is configured")

    issuer = f"{base_url.rstrip('/')}/g/{config['slug']}"
    scopes = list(config.get("required_scopes") or []) or None
    jwt_key = credentials.get("jwt_signing_key") or ""
    # FastMCP persists dynamic client registrations to ~/.local/share by default.
    # An in-memory store keeps the container root-filesystem read-only and means a
    # stateless pod: registrations are re-created on demand, which is what a client
    # does anyway when its registration stops resolving.
    from key_value.aio.stores.memory import MemoryStore

    client_storage = MemoryStore()

    common = {
        "client_id": credentials["client_id"],
        "client_secret": credentials.get("client_secret") or "",
        "base_url": issuer,
        "required_scopes": scopes,
        "jwt_signing_key": jwt_key,
        "cache_ttl_seconds": 300,
        "client_storage": client_storage,
    }

    match idp:
        case IdentityProvider.GITHUB:
            from fastmcp.server.auth.providers.github import GitHubProvider

            return GitHubProvider(**common)
        case IdentityProvider.GOOGLE:
            from fastmcp.server.auth.providers.google import GoogleProvider

            return GoogleProvider(
                **{**common, "required_scopes": scopes or ["openid", "email"]}
            )
        case IdentityProvider.AZURE:
            from fastmcp.server.auth.providers.azure import AzureProvider

            return AzureProvider(
                **common,
                tenant_id=credentials.get("tenant_id", "common"),
            )
        case IdentityProvider.DISCORD:
            from fastmcp.server.auth.providers.discord import DiscordProvider

            return DiscordProvider(**common)
        case IdentityProvider.OIDC:
            from fastmcp.server.auth.oidc_proxy import OIDCProxy

            return OIDCProxy(
                config_url=credentials["config_url"],
                client_id=credentials["client_id"],
                client_secret=credentials.get("client_secret") or "",
                base_url=issuer,
                jwt_signing_key=jwt_key,
            )
    raise ValueError(f"unsupported identity provider {idp}")

