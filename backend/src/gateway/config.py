"""Runtime configuration, entirely from environment variables.

Fail-fast by design: a gateway that boots with a weak session secret or a
missing database URL is worse than one that refuses to start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MIN_SECRET_BYTES = 32


class ConfigError(RuntimeError):
    """Environment is unusable; the process must not serve traffic."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    session_secret: str
    base_url: str
    host: str = "0.0.0.0"
    port: int = 8000
    admin_email: str | None = None
    admin_password: str | None = None
    config_file: str | None = None
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        session_secret = _require("OMG_SESSION_SECRET")
        if len(session_secret.encode()) < MIN_SECRET_BYTES:
            raise ConfigError(
                f"OMG_SESSION_SECRET must be at least {MIN_SECRET_BYTES} bytes"
            )
        base_url = _require("OMG_BASE_URL").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("OMG_BASE_URL must be an absolute http(s) URL")

        origins = tuple(
            origin.strip()
            for origin in os.environ.get("OMG_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )

        return cls(
            database_url=_require("OMG_DATABASE_URL"),
            session_secret=session_secret,
            base_url=base_url,
            host=os.environ.get("OMG_HOST", "0.0.0.0"),
            port=int(os.environ.get("OMG_PORT", "8000")),
            admin_email=os.environ.get("OMG_ADMIN_EMAIL") or None,
            admin_password=os.environ.get("OMG_ADMIN_PASSWORD") or None,
            config_file=os.environ.get("OMG_CONFIG_FILE") or None,
            cors_origins=origins,
        )


__all__ = ["ConfigError", "Settings", "MIN_SECRET_BYTES"]
