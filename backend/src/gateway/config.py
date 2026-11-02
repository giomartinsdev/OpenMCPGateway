"""Runtime configuration, entirely from environment variables.

Fail-fast by design: a gateway that boots with a weak session secret or a
missing database URL is worse than one that refuses to start.

`Settings` is a Pydantic model so the same validation applies whether it came
from the environment, from a test, or from a future config file. Secrets use
`SecretStr` so an accidental `log.info(settings)` cannot print them.
"""

import os

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

MIN_SECRET_BYTES = 32
DEFAULT_GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


class ConfigError(RuntimeError):
    """Environment is unusable; the process must not serve traffic."""


def _opt(name: str) -> str | None:
    return os.environ.get(name) or None


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: str
    # 32 bytes is the shortest length that makes a forgery attempt impractical.
    session_secret: SecretStr
    base_url: str
    host: str = "0.0.0.0"
    port: int = 8000
    admin_email: str | None = None
    admin_password: SecretStr | None = None
    config_file: str | None = None
    cors_origins: tuple[str, ...] = ()
    # Operator sign-in. All three must be present for Google login to be offered;
    # a half-configured IdP must not silently fall back to "no login at all".
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_discovery_url: str = DEFAULT_GOOGLE_DISCOVERY
    # Who may *become* an operator by signing in. Empty means nobody: an
    # installation with open sign-in is an installation anyone can administer.
    operator_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    log_level: str = "info"

    @field_validator("base_url")
    @classmethod
    def _absolute(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        return value

    @property
    def google_login_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @classmethod
    def from_env(cls) -> Settings:
        """Read the environment, reporting every failure as `ConfigError`.

        One exception type for "this process must not start" means the CLI can
        print a sentence instead of a traceback, and a missing variable looks
        like a bad value rather than a bug.
        """
        try:
            return cls._from_env()
        except ValidationError as exc:
            # Pydantic's own message is three lines and a URL; the useful part is
            # "base_url: must be an absolute http(s) URL".
            raise ConfigError(
                "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
            ) from exc
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    @classmethod
    def _from_env(cls) -> Settings:
        session_secret = _require("OMG_SESSION_SECRET")
        if len(session_secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(
                f"OMG_SESSION_SECRET must be at least {MIN_SECRET_BYTES} bytes"
            )

        origins = tuple(
            origin.strip()
            for origin in os.environ.get("OMG_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        admin_password = _opt("OMG_ADMIN_PASSWORD")
        google_secret = _opt("OMG_GOOGLE_CLIENT_SECRET")

        return cls(
            database_url=_require("OMG_DATABASE_URL"),
            session_secret=SecretStr(session_secret),
            base_url=_require("OMG_BASE_URL"),
            host=os.environ.get("OMG_HOST", "0.0.0.0"),
            port=int(os.environ.get("OMG_PORT", "8000")),
            admin_email=_opt("OMG_ADMIN_EMAIL"),
            admin_password=SecretStr(admin_password) if admin_password else None,
            config_file=_opt("OMG_CONFIG_FILE"),
            cors_origins=origins,
            google_client_id=_opt("OMG_GOOGLE_CLIENT_ID"),
            google_client_secret=(
                SecretStr(google_secret) if google_secret else None
            ),
            google_discovery_url=(
                _opt("OMG_GOOGLE_DISCOVERY_URL") or DEFAULT_GOOGLE_DISCOVERY
            ),
            operator_allowlist=tuple(
                email.strip().lower()
                for email in os.environ.get("OMG_OPERATOR_ALLOWLIST", "").split(",")
                if email.strip()
            ),
            log_level=os.environ.get("OMG_LOG_LEVEL", "info"),
        )


__all__ = [
    "DEFAULT_GOOGLE_DISCOVERY",
    "MIN_SECRET_BYTES",
    "ConfigError",
    "Settings",
]
