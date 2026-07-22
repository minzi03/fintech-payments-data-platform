"""Environment-backed application configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


class StorageBackendKind(StrEnum):
    """Supported immutable object-storage implementations."""

    LOCAL = "local"
    MINIO = "minio"


def _parse_positive_number(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a positive number") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be a positive number")
    return parsed


def _parse_boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"{name} must be true or false")
    return normalized == "true"


def _validate_bucket_name(value: str, name: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value) is None:
        raise ConfigurationError(f"{name} must be a valid 3-63 character S3 bucket name")
    return value


@dataclass(frozen=True, slots=True)
class MinioSettings:
    """Typed MinIO client settings with secret-bearing fields hidden from repr."""

    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    secure: bool = False
    region: str = "us-east-1"
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    max_retries: int = 3

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> MinioSettings:
        """Validate MinIO endpoint, credentials, TLS mode, timeouts, and retries."""
        raw_endpoint = environ.get("MINIO_ENDPOINT", "").strip()
        access_key = environ.get("MINIO_ACCESS_KEY", "").strip()
        secret_key = environ.get("MINIO_SECRET_KEY", "")
        secure = _parse_boolean(environ.get("MINIO_SECURE", "false"), "MINIO_SECURE")
        region = environ.get("MINIO_REGION", "us-east-1").strip()
        missing = [
            name
            for name, value in (
                ("MINIO_ENDPOINT", raw_endpoint),
                ("MINIO_ACCESS_KEY", access_key),
                ("MINIO_SECRET_KEY", secret_key),
                ("MINIO_REGION", region),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(f"Missing MinIO configuration variables: {', '.join(missing)}")

        parsed = urlsplit(raw_endpoint if "://" in raw_endpoint else f"//{raw_endpoint}")
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("MINIO_ENDPOINT scheme must be http or https")
        if parsed.username or parsed.password:
            raise ConfigurationError("MINIO_ENDPOINT must not contain credentials")
        if not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ConfigurationError("MINIO_ENDPOINT must contain only a host and optional port")
        if parsed.scheme and secure != (parsed.scheme == "https"):
            raise ConfigurationError("MINIO_SECURE must match the MINIO_ENDPOINT scheme")
        try:
            port = parsed.port
        except ValueError as error:
            raise ConfigurationError("MINIO_ENDPOINT port is invalid") from error
        host = parsed.hostname
        endpoint = f"{host}:{port}" if port is not None else host

        raw_retries = environ.get("MINIO_MAX_RETRIES", "3").strip()
        try:
            max_retries = int(raw_retries)
        except ValueError as error:
            raise ConfigurationError("MINIO_MAX_RETRIES must be an integer") from error
        if not 0 <= max_retries <= 10:
            raise ConfigurationError("MINIO_MAX_RETRIES must be between 0 and 10")

        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
            connect_timeout_seconds=_parse_positive_number(
                environ.get("MINIO_CONNECT_TIMEOUT_SECONDS", "5"),
                "MINIO_CONNECT_TIMEOUT_SECONDS",
            ),
            read_timeout_seconds=_parse_positive_number(
                environ.get("MINIO_READ_TIMEOUT_SECONDS", "30"),
                "MINIO_READ_TIMEOUT_SECONDS",
            ),
            max_retries=max_retries,
        )

    @property
    def endpoint_label(self) -> str:
        """Return a credential-free endpoint label suitable for diagnostics."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"


@dataclass(frozen=True, slots=True)
class StorageSettings:
    """Backend selection plus local roots and logical object-store buckets."""

    backend: StorageBackendKind
    local_bronze_root: Path
    local_quarantine_root: Path
    bronze_bucket: str
    quarantine_bucket: str
    local_silver_root: Path = Path("data/silver")
    silver_bucket: str = "fintech-silver"
    minio: MinioSettings | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        *,
        backend_override: str | None = None,
    ) -> StorageSettings:
        """Build storage settings without requiring MinIO secrets for local mode."""
        raw_backend = (backend_override or environ.get("STORAGE_BACKEND", "local")).strip().lower()
        try:
            backend = StorageBackendKind(raw_backend)
        except ValueError as error:
            raise ConfigurationError("STORAGE_BACKEND must be local or minio") from error

        bronze_bucket = _validate_bucket_name(
            environ.get("MINIO_BRONZE_BUCKET", "fintech-bronze").strip(),
            "MINIO_BRONZE_BUCKET",
        )
        quarantine_bucket = _validate_bucket_name(
            environ.get("MINIO_QUARANTINE_BUCKET", "fintech-quarantine").strip(),
            "MINIO_QUARANTINE_BUCKET",
        )
        silver_bucket = _validate_bucket_name(
            environ.get("MINIO_SILVER_BUCKET", "fintech-silver").strip(),
            "MINIO_SILVER_BUCKET",
        )
        return cls(
            backend=backend,
            local_bronze_root=Path(environ.get("SETTLEMENT_BRONZE_DIR", "data/bronze")),
            local_quarantine_root=Path(environ.get("SETTLEMENT_QUARANTINE_DIR", "data/quarantine")),
            bronze_bucket=bronze_bucket,
            quarantine_bucket=quarantine_bucket,
            local_silver_root=Path(environ.get("SILVER_LOCAL_ROOT", "data/silver")),
            silver_bucket=silver_bucket,
            minio=MinioSettings.from_env(environ) if backend is StorageBackendKind.MINIO else None,
        )


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
