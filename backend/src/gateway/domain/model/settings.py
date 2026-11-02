"""How an operator signs in to the panel — one aggregate per deployment.

It is an aggregate rather than a config row because saving it is an auditable
event: who turned Google sign-in on, and when, is the kind of question that gets
asked after an incident.
"""

from collections.abc import Iterable, Sequence

from gateway.domain.events import SIGN_IN_AGGREGATE, Event, SignInConfigured
from gateway.domain.model.base import DomainError, _Aggregate


class SignInSettings(_Aggregate):
    """How an operator logs in to the panel — one per deployment."""

    id: str = SIGN_IN_AGGREGATE
    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_uri: str = ""
    operator_allowlist: tuple[str, ...] = ()
    version: int = 0

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> SignInSettings:
        settings = cls()
        for event in events:
            settings._apply(event)
            settings.version = event.sequence
        return settings

    def _apply(self, event: Event) -> None:
        match event:
            case SignInConfigured():
                self.google_client_id = event.google_client_id
                self.google_client_secret = event.google_client_secret
                self.redirect_uri = event.redirect_uri
                self.operator_allowlist = tuple(event.operator_allowlist)

    @property
    def enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def decide_configure(
        self,
        *,
        google_client_id: str,
        google_client_secret: str,
        redirect_uri: str,
        operator_allowlist: Sequence[str] = (),
    ) -> list[Event]:
        """Validate, keeping the stored secret when the caller omits it.

        The panel never receives the secret back, so it cannot send it again. An
        empty secret therefore means "unchanged", not "clear it" — otherwise every
        edit that touches anything else silently breaks sign-in.
        """
        client_id = google_client_id.strip()
        redirect = redirect_uri.strip()
        secret = google_client_secret or self.google_client_secret

        if not client_id:
            raise DomainError("google_client_id is required")
        if not secret:
            raise DomainError("google_client_secret is required")
        if not redirect:
            # Naming the field beats "invalid configuration": the failure this
            # prevents shows up at the provider, not here.
            raise DomainError(
                "redirect_uri is required — it must match the authorised redirect "
                "URI registered at the provider exactly"
            )
        if not redirect.startswith(("http://", "https://")):
            raise DomainError("redirect_uri must be an absolute http(s) URL")

        allowlist = tuple(
            email.strip().lower() for email in operator_allowlist if email.strip()
        )
        for email in allowlist:
            if "@" not in email:
                raise DomainError(f"'{email}' is not an email address")

        return [
            SignInConfigured(
                aggregate_id=self.id,
                google_client_id=client_id,
                google_client_secret=secret,
                redirect_uri=redirect,
                operator_allowlist=allowlist,
            )
        ]


__all__ = ["SignInSettings"]
