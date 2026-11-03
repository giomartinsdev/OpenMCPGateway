"""Command side (CQRS writes): load stream -> decide -> append -> project.

Every method returns the new events so callers can feed the proxy cache without
re-reading the database. Handlers never touch read models directly; `commit`
does that, which keeps the write and read models from drifting.

The handler depends on the `EventStore` port and the `ProjectionRunner` class,
not on Postgres. Persistence is an adapter, and swapping it is a wiring change.
"""

import secrets
from collections.abc import Sequence

from gateway.application.ports import EventStore
from gateway.domain.events import (
    ApiKeyIssued,
    AuthMode,
    Event,
    IdentityProvider,
    OperatorLoggedIn,
)
from gateway.domain.model import (
    JOIN_CODE_ALPHABET,
    DomainError,
    Gateway,
    Operator,
    Role,
    Tenant,
)
from gateway.domain.permissions import DEFAULT_ROLE_IDS, OWNER_ROLE
from gateway.infrastructure.eventstore.postgres import ConcurrencyError
from gateway.infrastructure.eventstore.projections import ProjectionRunner
from gateway.infrastructure.security import (
    generate_api_key,
    hash_password,
    open_sealed,
    seal,
)
from gateway.log import log


class CommandHandler:
    def __init__(
        self,
        store: EventStore,
        projections: ProjectionRunner,
        *,
        sealing_secret: str = "",
    ) -> None:
        self._store = store
        self._projections = projections
        #: Derived from the deployment's session secret. Empty in tests that do
        #: not care about reveal, which is why sealing is skipped rather than
        #: raising: a key that cannot be revealed still works for auth.
        self._sealing_secret = sealing_secret

    # -- internal ---------------------------------------------------------

    async def _commit(
        self, aggregate_id: str, events: list[Event], expected_version: int, actor: str
    ) -> list[Event]:
        try:
            appended = await self._store.append(
                events, expected_version=expected_version, actor=actor
            )
        except ConcurrencyError as exc:
            log.warning(
                "command.conflict",
                aggregate_id=aggregate_id,
                expected_version=expected_version,
                error=str(exc),
            )
            raise
        # Project synchronously: the dashboard must not show stale state after a
        # write. Ordering within a stream is guaranteed by `sequence`.
        for event in appended:
            await self._projections.apply(event.sequence, event)
        await self._store.save_checkpoint(
            self._projections.name, appended[-1].sequence
        )
        log.info(
            "command.committed",
            aggregate_id=aggregate_id,
            events=[event.EVENT for event in appended],
            actor=actor,
        )
        return appended

    def _seal(self, plaintext: str) -> str:
        """Sealed copy, or empty when this deployment has no sealing secret."""
        return seal(plaintext, secret=self._sealing_secret) if self._sealing_secret else ""

    async def _load_gateway(self, gateway_id: str) -> Gateway:
        events = await self._store.load_stream(gateway_id)
        return Gateway.from_events(gateway_id, events)

    # -- operators --------------------------------------------------------

    async def register_operator(
        self, *, email: str, password: str, is_admin: bool = False,
        role_ids: Sequence[str] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Create an account. `role_ids` wins over `is_admin` when both are given.

        `is_admin` remains for the bootstrap path and for any caller that predates
        roles; it folds to the owner role in the aggregate. `tenant_id=None` is a
        new signup with no tenant yet — it sees onboarding and nothing else.
        """
        existing = await self._store.fetch_operator_by_email(email)
        if existing:
            raise DomainError("email already registered")
        operator_id = f"op_{secrets.token_hex(8)}"
        operator = Operator(id=operator_id)
        events = operator.decide_register(
            email=email,
            password_hash=hash_password(password),
            is_admin=is_admin,
            role_ids=() if role_ids is None else tuple(role_ids),
            tenant_id=tenant_id,
        )
        await self._commit(operator_id, events, 0, actor=email)
        await self._store.index_operator_email(email, operator_id)
        return operator_id

    async def register_sso_operator(
        self, *, email: str, subject: str, is_admin: bool = False,
        role_ids: Sequence[str] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Create an operator whose password nobody knows.

        The aggregate requires a password hash, and there is no such thing as a
        "no password" operator. Rather than give SSO users a blank or guessable
        password, this stores a hash of random bytes that were never revealed:
        the only way in is the identity provider. A later password reset can
        replace it.
        """
        del subject  # The provider's `sub` is recorded on the session, not here.
        return await self.register_operator(
            email=email,
            password=secrets.token_urlsafe(48),
            is_admin=is_admin,
            role_ids=role_ids,
            tenant_id=tenant_id,
        )

    async def authenticate_operator(
        self, *, email: str, password: str, ip: str | None = None
    ) -> Operator | None:
        from gateway.infrastructure.security import verify_password

        operator_id = await self._store.fetch_operator_by_email(email)
        if not operator_id:
            log.warning("operator.login_failed", email=email, reason="unknown_email")
            return None
        events = await self._store.load_stream(operator_id)
        operator = Operator.from_events(operator_id, events)
        if not operator.exists or not verify_password(operator.password_hash, password):
            log.warning("operator.login_failed", email=email, reason="bad_password")
            return None
        await self._commit(
            operator_id,
            [OperatorLoggedIn(aggregate_id=operator_id, ip=ip, actor=email)],
            events[-1].sequence if events else 0,
            actor=email,
        )
        log.info("operator.logged_in", email=email, ip=ip)
        return operator

    async def assign_roles(
        self, operator_id: str, *, role_ids: Sequence[str], actor: str = "system"
    ) -> list[Event]:
        events_in_stream = await self._store.load_stream(operator_id)
        operator = Operator.from_events(operator_id, events_in_stream)
        events = operator.decide_assign_roles(role_ids)
        return await self._commit(
            operator_id,
            events,
            events_in_stream[-1].sequence if events_in_stream else 0,
            actor=actor,
        )

    # -- tenants ----------------------------------------------------------

    async def ensure_tenant(self, *, tenant_id: str, name: str) -> None:
        """Create a tenant if the log has no such stream yet. Boot-time only.

        Two callers need a tenant that predates the concept: the env bootstrap
        admin, and accounts that were written when one deployment was one tenant.
        Both must get a real row, because a tenant without one has no join code
        and its members cannot invite anyone.
        """
        stream = await self._store.load_stream(tenant_id)
        tenant = Tenant.from_events(tenant_id, stream)
        if tenant.exists:
            return
        events = tenant.decide_create(name=name, join_code=await self._free_join_code())
        await self._commit(
            tenant_id, events, stream[-1].sequence if stream else 0, actor="system"
        )

    async def create_tenant(
        self, *, name: str, owner_id: str, actor: str = "system"
    ) -> dict[str, str]:
        """Create a tenant and make the account its owner, in that order.

        One command because the two must not be separable: a tenant with no owner
        is unreachable, and an account pointing at a tenant that does not exist
        reads as "no tenant" on the next request.
        """
        stream = await self._store.load_stream(owner_id)
        operator = Operator.from_events(owner_id, stream)
        if not operator.exists:
            raise DomainError("operator does not exist")
        if operator.tenant_id is not None:
            raise DomainError(
                "this account already belongs to a tenant; leaving is not supported"
            )

        tenant_id = f"tn_{secrets.token_hex(8)}"
        draft = Tenant(id=tenant_id)
        events = draft.decide_create(
            name=name, join_code=await self._free_join_code()
        )
        await self._commit(tenant_id, events, 0, actor=actor)

        joined = operator.decide_join_tenant(
            tenant_id=tenant_id, role_ids=(OWNER_ROLE,)
        )
        await self._commit(
            owner_id, joined, stream[-1].sequence if stream else 0, actor=actor
        )
        return {"tenant_id": tenant_id, "name": name, "join_code": events[0].join_code}

    async def join_tenant(
        self, *, join_code: str, operator_id: str, actor: str = "system"
    ) -> dict[str, str]:
        """Add an account to the tenant whose code it knows.

        The same refusal for "no such code" and "already a member": telling them
        apart turns a guess into a search, and neither is the caller's business.
        """
        tenant = await self._store.fetch_tenant_by_code(join_code)
        stream = await self._store.load_stream(operator_id)
        operator = Operator.from_events(operator_id, stream)
        if tenant is None or not operator.exists:
            raise DomainError("that join code is not valid")
        events = operator.decide_join_tenant(
            tenant_id=tenant["id"], role_ids=DEFAULT_ROLE_IDS
        )
        await self._commit(
            operator_id, events, stream[-1].sequence if stream else 0, actor=actor
        )
        return {"tenant_id": tenant["id"], "tenant_name": tenant["name"]}

    async def _free_join_code(self) -> str:
        """A code nobody else has, checked before the tenant exists.

        Retried rather than assumed: 32^8 is large, and a collision would be a
        tenant whose code joins someone else's.
        """
        for _ in range(5):
            code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(8))
            if not await self._store.join_code_taken(code):
                return code
        raise DomainError("could not allocate a join code; try again")

    # -- roles ------------------------------------------------------------

    async def define_role(
        self, *, tenant_id: str, role_id: str | None = None, name: str,
        permissions: Sequence[str], actor: str = "system",
    ) -> str:
        role_id = role_id or f"role_{secrets.token_hex(6)}"
        stream = await self._store.load_stream(role_id)
        role = Role.from_events(role_id, stream)
        events = role.decide_define(
            tenant_id=tenant_id, name=name, permissions=permissions
        )
        await self._commit(
            role_id, events, stream[-1].sequence if stream else 0, actor=actor
        )
        return role_id

    async def delete_role(self, role_id: str, *, actor: str = "system") -> list[Event]:
        stream = await self._store.load_stream(role_id)
        role = Role.from_events(role_id, stream)
        events = role.decide_delete()
        return await self._commit(
            role_id, events, stream[-1].sequence if stream else 0, actor=actor
        )

    # -- gateways ---------------------------------------------------------

    async def create_gateway(
        self,
        *,
        tenant_id: str,
        slug: str,
        name: str,
        target_url: str,
        description: str = "",
        auth_mode: AuthMode = AuthMode.NONE,
        idp: IdentityProvider | None = None,
        actor: str = "system",
    ) -> str:
        if await self._store.resolve_slug(slug):
            raise DomainError(f"slug '{slug}' already taken")
        gateway_id = f"gw_{secrets.token_hex(8)}"
        # The aggregate is empty, so decide_create validates from scratch.
        draft = Gateway(id=gateway_id)
        events = draft.decide_create(
            tenant_id=tenant_id,
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

    async def update_gateway(
        self,
        gateway_id: str,
        *,
        name: str | None = None,
        target_url: str | None = None,
        description: str | None = None,
        actor: str = "system",
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_update(
            name=name, target_url=target_url, description=description
        )
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def configure_auth(
        self,
        gateway_id: str,
        *,
        auth_mode: AuthMode,
        idp: IdentityProvider | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        config_url: str | None = None,
        required_scopes: tuple[str, ...] = (),
        upstream_token: str | None = None,
        actor: str = "system",
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_configure_auth(
            auth_mode=auth_mode,
            idp=idp,
            client_id=client_id,
            client_secret=client_secret,
            config_url=config_url,
            required_scopes=required_scopes,
            upstream_token=upstream_token,
        )
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def set_rate_limit(
        self, gateway_id: str, *, requests_per_minute: int, actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_set_rate_limit(requests_per_minute=requests_per_minute)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def allow_principal(
        self, gateway_id: str, *, subject: str, note: str = "", actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_allow(subject=subject, note=note)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def revoke_principal(
        self, gateway_id: str, *, subject: str, actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_revoke(subject=subject)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def disable_gateway(
        self, gateway_id: str, *, reason: str = "", actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_disable(reason=reason)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def enable_gateway(
        self, gateway_id: str, *, actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_enable()
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def delete_gateway(
        self, gateway_id: str, *, actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_delete()
        appended = await self._commit(gateway_id, events, gateway.version, actor=actor)
        await self._store.release_slug(gateway.slug)
        return appended

    # -- api keys ---------------------------------------------------------

    async def issue_api_key(
        self, gateway_id: str, *, label: str, actor: str = "system"
    ) -> tuple[str, str]:
        """Returns (plaintext, key_id). The plaintext is sealed for one reveal."""
        gateway = await self._load_gateway(gateway_id)
        plaintext, key_hash = generate_api_key()
        key_id = f"key_{secrets.token_hex(6)}"
        events = gateway.decide_issue_key(
            key_id=key_id,
            label=label,
            key_hash=key_hash,
            # A distinct subject per key, so one noisy key cannot exhaust another
            # key's budget on the same gateway.
            subject=f"api-key:{key_id}",
            sealed=self._seal(plaintext),
        )
        await self._commit(gateway_id, events, gateway.version, actor=actor)
        return plaintext, key_id

    async def rotate_api_key(
        self, gateway_id: str, *, key_id: str, actor: str = "system"
    ) -> tuple[str, str]:
        """Replace a key in one append: the new one exists before the old one dies.

        Two appends would leave a window where neither key works (and, if the
        second failed, a working key nobody holds). Rotation is a single event
        batch, so a caller that swaps its config sees no gap.
        """
        gateway = await self._load_gateway(gateway_id)
        label = _key_label(await self._store.load_stream(gateway_id), key_id)
        plaintext, key_hash = generate_api_key()
        new_id = f"key_{secrets.token_hex(6)}"
        events = [
            *gateway.decide_issue_key(
                key_id=new_id,
                label=label,
                key_hash=key_hash,
                subject=f"api-key:{new_id}",
                sealed=self._seal(plaintext),
            ),
            *gateway.decide_revoke_key(key_id=key_id),
        ]
        await self._commit(gateway_id, events, gateway.version, actor=actor)
        return plaintext, new_id

    async def reveal_api_key(
        self,
        gateway_id: str,
        *,
        key_id: str,
        sealed: str,
        actor: str = "system",
    ) -> str | None:
        """Open a sealed plaintext and immediately make it unreadable again.

        The caller reads the sealed column (the handler has no query side), this
        opens it, and the appended `ApiKeyRevealed` wipes the column in the
        projection. One trip, on purpose: the eye icon exists so the operator who
        issued a key can retrieve it once, not so the panel becomes a secret
        store.
        """
        plaintext = open_sealed(sealed, secret=self._sealing_secret)
        if plaintext is None:
            return None
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_reveal_key(key_id=key_id)
        await self._commit(gateway_id, events, gateway.version, actor=actor)
        return plaintext

    async def set_gateway_tools(
        self, gateway_id: str, *, tools: Sequence[str], actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_set_tools(tools=tools)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)

    async def revoke_api_key(
        self, gateway_id: str, *, key_id: str, actor: str = "system"
    ) -> list[Event]:
        gateway = await self._load_gateway(gateway_id)
        events = gateway.decide_revoke_key(key_id=key_id)
        return await self._commit(gateway_id, events, gateway.version, actor=actor)


__all__ = ["CommandHandler"]


def _key_label(events: Sequence[Event], key_id: str) -> str:
    """The label the key was issued with, so a rotation keeps its name.

    Read from the stream rather than the read model: the write side already has
    the events, and a rotated key that silently lost its label is a key nobody
    can identify in the list.
    """
    for event in reversed(events):
        if isinstance(event, ApiKeyIssued) and event.key_id == key_id:
            return event.label
    raise DomainError("key not found")
