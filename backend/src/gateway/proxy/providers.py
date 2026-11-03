"""Building the FastMCP OAuth provider for one gateway, per identity provider."""

from typing import Any

from fastmcp.server.auth import AuthProvider, MultiAuth

from gateway.domain.events import AuthMode, IdentityProvider
from gateway.proxy.identity import GatewayNotReady

# OAuth state that outlives the provider instance, keyed by gateway id.
#
# `OAuthProxy` keeps three things a token depends on in `client_storage`: the
# `jti → upstream token` mapping, the upstream tokens themselves, and refresh
# metadata. A JWT it issued is self-contained *except* for the jti, which is a
# pointer into that store. Build a fresh store per install — which `refresh()`
# does on every unknown path — and the pointer dangles: the token still verifies
# by signature and then reads back as unauthenticated, so clients get a 401
# moments after a successful login.
#
# So the store is per gateway, not per install: `_CLIENT_STORES` caches one view
# per gateway id, and every rebuild of that gateway reads the same rows.
#
# And the rows live in Postgres, not in the process. In memory they died with the
# pod, so a rollout logged every connected client out — the same 401, one deploy
# later. `configure_client_storage` points the shared backend at the app
# database; without it (unit tests that never boot a container) the store stays
# per-process, which is not a deployment anyone should run.
_CLIENT_STORES: dict[str, Any] = {}
_BACKEND: Any = None
_BACKEND_STORE: Any = None


async def configure_client_storage(dsn: str, *, secret: str) -> None:
    """Point OAuth state at Postgres so it survives rollouts and multiple replicas.

    One table, one connection pool, shared by every gateway: each gateway reads
    the table through a collection prefix, which is what keeps two gateways'
    `mcp-jti-mappings` apart. Values are encrypted at rest because upstream
    access and refresh tokens are bearer credentials — the same reason FastMCP
    encrypts the file store it would have created itself.

    The table is created here rather than on first use: a database user without
    DDL rights should fail the boot, not the first sign-in.
    """
    global _BACKEND, _BACKEND_STORE

    from key_value.aio.stores.postgresql import PostgreSQLStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    _BACKEND_STORE = PostgreSQLStore(url=_asyncpg_dsn(dsn), table_name="oauth_state")
    await _BACKEND_STORE.setup()
    _BACKEND = FernetEncryptionWrapper(
        key_value=_BACKEND_STORE,
        source_material=secret,
        salt="openmcp-gateway/oauth-state",
        # A rotated secret makes old rows unreadable; treating that as a cache
        # miss costs one re-login instead of a 500 on every existing token.
        raise_on_decryption_error=False,
    )
    _CLIENT_STORES.clear()


async def close_client_storage() -> None:
    """Release the pool. Called on shutdown; a restart reconfigures."""
    global _BACKEND, _BACKEND_STORE
    if _BACKEND_STORE is not None:
        await _BACKEND_STORE.close()
    _BACKEND = None
    _BACKEND_STORE = None
    _CLIENT_STORES.clear()


def _asyncpg_dsn(dsn: str) -> str:
    """Drop the SQLAlchemy driver suffix: asyncpg only speaks `postgresql://`."""
    for suffix in ("+asyncpg", "+psycopg2"):
        dsn = dsn.replace(suffix, "")
    return dsn


def _client_store(gateway_id: str) -> Any:
    """The OAuth store for one gateway: a prefix over the shared backend."""
    store = _CLIENT_STORES.get(gateway_id)
    if store is not None:
        return store

    if _BACKEND is None:
        from key_value.aio.stores.memory import MemoryStore

        store = MemoryStore()
    else:
        from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

        store = PrefixCollectionsWrapper(key_value=_BACKEND, prefix=gateway_id)
    _CLIENT_STORES[gateway_id] = store
    return store


async def forget_client_store(gateway_id: str) -> None:
    """Drop a gateway's OAuth state. Called when the gateway stops serving.

    Dropping the cached view is not enough once the rows are durable: the tokens
    would outlive the gateway forever, still valid against the upstream. Delete
    the prefixed collections, enumerated from the backend rather than listed by
    hand so a new FastMCP collection is cleaned up without a change here.
    """
    _CLIENT_STORES.pop(gateway_id, None)
    if _BACKEND_STORE is None:
        return

    prefix = f"{gateway_id}__"
    for collection in await _BACKEND_STORE.collections():
        if collection.startswith(prefix):
            await _BACKEND_STORE.destroy_collection(collection)


class APIKeyVerifier(AuthProvider):
    """The gateway's own keys, as a FastMCP verifier.

    Lives here, not in the guard, because FastMCP enforces bearer auth underneath
    the middleware: a key the guard admits but this never sees would be a 401 on
    a request that was already counted as allowed.
    """

    def __init__(self, gateway_id: str, read_models: Any) -> None:
        super().__init__()
        self._gateway_id = gateway_id
        self._read_models = read_models

    async def verify_token(self, token: str) -> Any:
        from mcp.server.auth.provider import AccessToken

        from gateway.infrastructure.security import hash_api_key

        subject = await self._read_models.subject_for_key_hash(
            self._gateway_id, hash_api_key(token)
        )
        if not subject:
            return None
        return AccessToken(
            token=token, client_id="api_key", scopes=[], subject=subject
        )

class _AnySourceScopes(MultiAuth):
    """`MultiAuth` that verifies every source but enforces no single one's scopes.

    A token minted by a *different* issuer cannot carry the login provider's
    scopes (GitHub's `user`, Google's `email`), so the transport's scope check
    would 403 a token the gateway deliberately accepts. Scopes are still
    advertised in the challenge and in the protected-resource metadata; who may
    call is the guard's allowlist, not a claim comparison between unrelated
    issuers.

    `required_scopes=[]` is what keeps the transport check quiet, so the
    challenge is overridden to keep asking the login provider for its own scopes
    — dropping them would send every OAuth client to the IdP with none.
    """

    def __init__(self, server: AuthProvider, verifiers: dict[str, AuthProvider]):
        super().__init__(server=server, verifiers=verifiers, required_scopes=[])

    def get_challenge_scopes(
        self, required_scopes: list[str] | None = None
    ) -> list[str]:
        if self.server is not None:
            return self.server.get_challenge_scopes()
        return super().get_challenge_scopes(required_scopes)

def build_auth_provider(
    config: dict[str, Any], *, base_url: str, credentials: dict[str, str],
    read_models: Any = None,
) -> AuthProvider | None:
    """Instantiate the auth for a gateway, or None when it needs no provider.

    Shapes, one entry point:

    * **no provider** — public gateways and pure API-key gateways. Keys are
      verified by the guard itself and need no discovery document.
    * **one login, maybe more** — the OAuth proxy, wrapped in `MultiAuth` when
      the gateway also accepts keys or extra issuers. `MultiAuth` delegates the
      routes and metadata to the wrapped server, so the browser flow is
      unchanged and the extra sources contribute only verification.

    The key verifier is part of this list rather than a guard-side special case
    because FastMCP enforces auth underneath the guard. A guard that accepted a
    key the transport then rejected would 401 a request it had just admitted.

    Raises `GatewayNotReady` when an OAuth gateway has no credentials yet. That is
    a normal intermediate state — you create a gateway, then paste the client id —
    and it must fail *closed*: falling back to "no provider" would silently turn
    an OAuth route into an unauthenticated one.
    """
    mode = AuthMode(config["auth_mode"])
    if mode is not AuthMode.OAUTH:
        return None

    server = _oauth_server(config, base_url=base_url, credentials=credentials)
    extra: dict[str, AuthProvider] = dict(_verifiers(config))
    if config.get("allow_api_key") and read_models is not None:
        extra["api_key"] = APIKeyVerifier(config["id"], read_models)
    if not extra:
        return server
    # Labels become the source ids: they are what the caller's token is logged
    # as, and the domain refuses duplicates, so `MultiAuth` cannot see two
    # sources with the same name.
    return _AnySourceScopes(server=server, verifiers=extra)

def _verifiers(config: dict[str, Any]) -> dict[str, AuthProvider]:
    """JWT verifiers for issuers the operator configured, keyed by their label."""
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    built: dict[str, AuthProvider] = {}
    for spec in config.get("verifiers") or []:
        built[spec["label"]] = JWTVerifier(
            jwks_uri=spec["jwks_uri"],
            issuer=spec["issuer"],
            # Empty audience means "this issuer does not set one"; passing ""
            # would compare against an empty claim and reject every token.
            audience=spec.get("audience") or None,
        )
    return built

def _oauth_server(
    config: dict[str, Any], *, base_url: str, credentials: dict[str, str]
) -> AuthProvider:
    """The provider that hosts this gateway's own login flow."""
    idp = IdentityProvider(config["idp"])
    if not credentials.get("client_id"):
        raise GatewayNotReady("OAuth is selected but no client_id is configured")
    if idp is IdentityProvider.OIDC and not credentials.get("config_url"):
        raise GatewayNotReady("OIDC is selected but no discovery URL is configured")

    issuer = f"{base_url.rstrip('/')}/g/{config['slug']}"
    scopes = list(config.get("required_scopes") or []) or None
    jwt_key = credentials.get("jwt_signing_key") or ""
    # FastMCP persists dynamic client registrations to ~/.local/share by default.
    # An in-memory store keeps the container root-filesystem read-only. It is
    # shared per gateway id, not per install — see `_CLIENT_STORES` for why.
    client_storage = _client_store(config["id"])

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
                client_storage=client_storage,
            )
    raise ValueError(f"unsupported identity provider {idp}")

