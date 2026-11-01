"""Command side (CQRS writes): load stream -> decide -> append -> project.

Every method returns the new events so callers can feed the proxy cache without
re-reading the database. Handlers never touch projections directly; `append`
does that, which keeps the write and read models from drifting.
"""

from __future__ import annotations

import secrets

from gateway.domain.events import (
    AuthMode,
    Event,
    IdentityProvider,
    OperatorLoggedIn,
)
from gateway.domain.model import DomainError, Gateway, Operator
from gateway.infrastructure.eventstore.postgres import (
    ConcurrencyError,
    PostgresEventStore,
)
from gateway.infrastructure.eventstore.projections import ProjectionRunner
from gateway.infrastructure.security import generate_api_key, hash_password


class CommandHandler:
    def __init__(self, store: PostgresEventStore, projections: ProjectionRunner):
        self._store = store
        self._projections = projections

    # -- internal ---------------------------------------------------------

    async def _commit(self, aggregate_id: str, events: list[Event],
                      expected_version: int, actor: str) -> list[Event]:
        appended = await self._store.append(
            events, expected_version=expected_version, actor=actor
        )
        # Project synchronously: the dashboard must not show stale state after a
        # write. Ordering within a stream is guaranteed by `sequence`.
        for event in appended:
            await self._projections.apply(event.sequence, event)
        await self._store.save_checkpoint(
            self._projections.name, appended[-1].sequence
        )
        return appended

    async def _load_gateway(self, gateway_id: str) -> tuple[Gateway, int]:
        events = await self._store.load_stream(gateway_id)
        gateway = Gateway.from_events(gateway_id, events)
        return gateway, (events[-1].sequence if events else 0)

    # -- operators --------------------------------------------------------

    async def register_operator(self, *, email: str, password: str,
                                is_admin: bool = False) -> str:
        existing = await self._store.fetch_operator_by_email(email)
        if existing:
            raise DomainError("email already registered")
        operator_id = f"op_{secrets.token_hex(8)}"
        operator = Operator(id=operator_id)
        events = operator.decide_register(
            email=email, password_hash=hash_password(password), is_admin=is_admin
        )
        await self._commit(operator_id, events, 0, actor=email)
        await self._store.index_operator_email(email, operator_id)
        return operator_id

    async def authenticate_operator(self, *, email: str, password: str,
                                    ip: str | None = None) -> Operator | None:
        from gateway.infrastructure.security import verify_password

        operator_id = await self._store.fetch_operator_by_email(email)
        if not operator_id:
            return None
        events = await self._store.load_stream(operator_id)
        operator = Operator.from_events(operator_id, events)
        if not operator.exists or not verify_password(operator.password_hash, password):
            return None
        await self._commit(
            operator_id,
            [OperatorLoggedIn(aggregate_id=operator_id, ip=ip, actor=email)],
            events[-1].sequence if events else 0,
            actor=email,
        )
        return operator

    # -- gateways ---------------------------------------------------------

    async def create_gateway(self, *, slug: str, name: str, target_url: str,
                             description: str = "",
                             auth_mode: AuthMode = AuthMode.NONE,
                             idp: IdentityProvider | None = None,
                             actor: str = "system") -> str:
        if await self._store.resolve_slug(slug):
            raise DomainError(f"slug '{slug}' already taken")
        gateway_id = f"gw_{secrets.token_hex(8)}"
        gateway = Gateway(id=gateway_id, slug=slug)
        # The aggregate is empty, so decide_create validates from scratch.
        draft = Gateway(id=gateway_id)
        events = draft.decide_create(
            slug=slug,
            name=name,
            target_url=target_url,
            description=description,
            auth_mode=auth_mode,
            idp=idp,
        )
        await self._store.claim_slug(slug, gateway_id)
        try:
            await self._commit(gateway_id, events, 0, actor=actor)
        except Exception:
            await self._store.release_slug(slug)
            raise
        return gateway_id

    async def update_gateway(self, gateway_id: str, *, name: str | None = None,
                             target_url: str | None = None,
                             description: str | None = None,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_update(
            name=name, target_url=target_url, description=description
        )
        return await self._commit(gateway_id, events, version, actor=actor)

    async def configure_auth(self, gateway_id: str, *, auth_mode: AuthMode,
                             idp: IdentityProvider | None = None,
                             client_id: str | None = None,
                             client_secret: str | None = None,
                             config_url: str | None = None,
                             required_scopes: tuple[str, ...] = (),
                             upstream_token: str | None = None,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_configure_auth(
            auth_mode=auth_mode,
            idp=idp,
            client_id=client_id,
            client_secret=client_secret,
            config_url=config_url,
            required_scopes=required_scopes,
            upstream_token=upstream_token,
        )
        return await self._commit(gateway_id, events, version, actor=actor)

    async def set_rate_limit(self, gateway_id: str, *, requests_per_minute: int,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_set_rate_limit(requests_per_minute=requests_per_minute)
        return await self._commit(gateway_id, events, version, actor=actor)

    async def allow_principal(self, gateway_id: str, *, subject: str,
                              note: str = "", actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_allow(subject=subject, note=note)
        return await self._commit(gateway_id, events, version, actor=actor)

    async def revoke_principal(self, gateway_id: str, *, subject: str,
                               actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_revoke(subject=subject)
        return await self._commit(gateway_id, events, version, actor=actor)

    async def disable_gateway(self, gateway_id: str, *, reason: str = "",
                              actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_disable(reason=reason)
        return await self._commit(gateway_id, events, version, actor=actor)

    async def enable_gateway(self, gateway_id: str, *,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_enable()
        return await self._commit(gateway_id, events, version, actor=actor)

    async def delete_gateway(self, gateway_id: str, *,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_delete()
        appended = await self._commit(gateway_id, events, version, actor=actor)
        await self._store.release_slug(gateway.slug)
        return appended

    # -- api keys ---------------------------------------------------------

    async def issue_api_key(self, gateway_id: str, *, label: str,
                            actor: str = "system") -> tuple[str, str]:
        """Returns (plaintext, key_id). Plaintext is not recoverable later."""
        gateway, version = await self._load_gateway(gateway_id)
        plaintext, key_hash = generate_api_key()
        key_id = f"key_{secrets.token_hex(6)}"
        events = gateway.decide_issue_key(
            key_id=key_id,
            label=label,
            key_hash=key_hash,
            # A distinct subject per key, so one noisy key cannot exhaust another
            # key's budget on the same gateway.
            subject=f"api-key:{key_id}",
        )
        await self._commit(gateway_id, events, version, actor=actor)
        return plaintext, key_id

    async def revoke_api_key(self, gateway_id: str, *, key_id: str,
                             actor: str = "system") -> list[Event]:
        gateway, version = await self._load_gateway(gateway_id)
        events = gateway.decide_revoke_key(key_id=key_id)
        return await self._commit(gateway_id, events, version, actor=actor)


__all__ = ["CommandHandler"]
