"""Environment-backed application configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """PostgreSQL connection settings without secret-bearing representations."""

    host: str
    port: int
    database: str
    user: str
    password: str
    database_url: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> DatabaseSettings:
        """Build settings from a supplied environment mapping."""
        database_url = environ.get("DATABASE_URL", "").strip() or None
        host = environ.get("POSTGRES_HOST", "").strip()
        database = environ.get("POSTGRES_DB", "").strip()
        user = environ.get("POSTGRES_USER", "").strip()
        password = environ.get("POSTGRES_PASSWORD", "")
        raw_port = environ.get("POSTGRES_PORT", "").strip()

        if database_url:
            parsed = urlsplit(database_url)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
                raise ConfigurationError("DATABASE_URL must be a valid PostgreSQL URL")
            host = host or parsed.hostname
            database = database or parsed.path.lstrip("/")
            user = user or (parsed.username or "")
            try:
                port = int(raw_port) if raw_port else (parsed.port or 5432)
            except ValueError as error:
                raise ConfigurationError("POSTGRES_PORT must be an integer") from error
        else:
            missing = [
                key
                for key, value in (
                    ("POSTGRES_HOST", host),
                    ("POSTGRES_PORT", raw_port),
                    ("POSTGRES_DB", database),
                    ("POSTGRES_USER", user),
                    ("POSTGRES_PASSWORD", password),
                )
                if not value
            ]
            if missing:
                raise ConfigurationError(
                    f"Missing database configuration variables: {', '.join(missing)}"
                )
            try:
                port = int(raw_port)
            except ValueError as error:
                raise ConfigurationError("POSTGRES_PORT must be an integer") from error

        if not 1 <= port <= 65535:
            raise ConfigurationError("POSTGRES_PORT must be between 1 and 65535")
        if not database:
            raise ConfigurationError("PostgreSQL database name must not be empty")

        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            database_url=database_url,
        )

    @property
    def connection_label(self) -> str:
        """Return a safe connection label that never includes user info or passwords."""
        return f"{self.host}:{self.port}/{self.database}"
