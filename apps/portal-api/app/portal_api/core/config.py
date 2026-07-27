"""Typed, environment-backed Portal API configuration."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalEnvironment(StrEnum):
    """Supported Portal API runtime environments."""

    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


class PortalApiSettings(BaseSettings):
    """Portal API settings with production safety validation."""

    model_config = SettingsConfigDict(
        env_prefix="PORTAL_API_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: PortalEnvironment = PortalEnvironment.LOCAL
    service_name: str = "portal-api"
    service_version: str = "0.1.0-dev"
    api_version: str = "v1"
    contract_version: str = "1.0.0"
    documentation_version: str = "portal-foundation-v1"
    build_sha: str = "local"
    build_time: str = "local"
    log_level: str = "INFO"
    log_format: str = "console"
    host: str = "127.0.0.1"
    port: int = Field(default=8010, ge=1, le=65535)
    allowed_origins: str = "http://localhost:3000"
    trusted_hosts: str = "localhost,127.0.0.1,portal-api"
    dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    readiness_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    health_cache_ttl_seconds: float = Field(default=2.0, ge=0, le=60)
    telemetry_enabled: bool = False
    openapi_enabled: bool = True
    development_identity_enabled: bool = False
    security_runtime_enabled: bool = False
    database_url: SecretStr | None = None
    oidc_provider_id: str = "local-keycloak"
    oidc_issuer: str = "http://localhost:8081/realms/fintech-portal"
    oidc_client_id: str = "fintech-portal"
    oidc_authorization_endpoint: str = (
        "http://localhost:8081/realms/fintech-portal/protocol/openid-connect/auth"
    )
    oidc_token_endpoint: str = (
        "http://portal-keycloak:8080/realms/fintech-portal/protocol/openid-connect/token"
    )
    oidc_jwks_uri: str = (
        "http://portal-keycloak:8080/realms/fintech-portal/protocol/openid-connect/certs"
    )
    oidc_redirect_uri: str = "http://localhost:3000/portal-api/v1/auth/callback"
    oidc_scopes: str = "openid profile"
    allowed_return_paths: str = "/,/system-status"
    login_intent_ttl_seconds: int = Field(default=300, gt=0, le=300)
    login_transaction_ttl_seconds: int = Field(default=300, gt=0, le=300)

    @property
    def allowed_origin_values(self) -> tuple[str, ...]:
        """Return normalized CORS origins."""
        return _csv_values(self.allowed_origins)

    @property
    def trusted_host_values(self) -> tuple[str, ...]:
        """Return normalized trusted hosts."""
        return _csv_values(self.trusted_hosts)

    @property
    def is_production(self) -> bool:
        return self.environment is PortalEnvironment.PRODUCTION

    @property
    def allowed_return_path_values(self) -> tuple[str, ...]:
        return _csv_values(self.allowed_return_paths)

    @property
    def oidc_scope_values(self) -> tuple[str, ...]:
        return tuple(scope for scope in self.oidc_scopes.split() if scope)

    @model_validator(mode="after")
    def validate_safety(self) -> PortalApiSettings:
        if self.api_version != "v1":
            raise ValueError("PORTAL_API_API_VERSION must be v1 for PR-PORTAL-001")
        if not self.allowed_origin_values:
            raise ValueError("PORTAL_API_ALLOWED_ORIGINS must not be empty")
        if not self.trusted_host_values:
            raise ValueError("PORTAL_API_TRUSTED_HOSTS must not be empty")
        for origin in self.allowed_origin_values:
            parsed = urlsplit(origin)
            if origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("PORTAL_API_ALLOWED_ORIGINS must contain explicit HTTP(S) origins")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("PORTAL_API_ALLOWED_ORIGINS entries must not contain paths")
        if self.log_format not in {"json", "console"}:
            raise ValueError("PORTAL_API_LOG_FORMAT must be json or console")
        if self.security_runtime_enabled:
            if self.database_url is None:
                raise ValueError(
                    "PORTAL_API_DATABASE_URL is required when the security runtime is enabled"
                )
            database_url = self.database_url.get_secret_value()
            if not database_url.startswith("postgresql+psycopg://"):
                raise ValueError("PORTAL_API_DATABASE_URL must use PostgreSQL with psycopg")
            if self.environment in {PortalEnvironment.STAGING, PortalEnvironment.PRODUCTION}:
                raise ValueError(
                    "Portal security runtime is authorized only for local/development environments"
                )
            self._validate_oidc_configuration()
        if self.is_production:
            if self.log_format != "json":
                raise ValueError("Production requires PORTAL_API_LOG_FORMAT=json")
            if self.openapi_enabled:
                raise ValueError("Production requires PORTAL_API_OPENAPI_ENABLED=false")
            if self.development_identity_enabled:
                raise ValueError(
                    "PORTAL_API_DEVELOPMENT_IDENTITY_ENABLED is forbidden in production"
                )
            if "*" in self.trusted_host_values:
                raise ValueError("Wildcard trusted hosts are forbidden in production")
            if any(origin.startswith("http://") for origin in self.allowed_origin_values):
                raise ValueError("Production CORS origins must use HTTPS")
            if self.build_sha == "local" or self.build_time == "local":
                raise ValueError("Production requires immutable build SHA and build time")
        return self

    def _validate_oidc_configuration(self) -> None:
        if not self.allowed_return_path_values or any(
            not value.startswith("/") or value.startswith("//") or "\\" in value
            for value in self.allowed_return_path_values
        ):
            raise ValueError("PORTAL_API_ALLOWED_RETURN_PATHS must contain local absolute paths")
        if "openid" not in self.oidc_scope_values:
            raise ValueError("PORTAL_API_OIDC_SCOPES must include openid")
        for field_name in (
            "oidc_issuer",
            "oidc_authorization_endpoint",
            "oidc_token_endpoint",
            "oidc_jwks_uri",
            "oidc_redirect_uri",
        ):
            value = getattr(self, field_name)
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"PORTAL_API_{field_name.upper()} must be an absolute HTTP(S) URL")
        if self.environment not in {PortalEnvironment.LOCAL, PortalEnvironment.TEST}:
            external_urls = (
                self.oidc_issuer,
                self.oidc_authorization_endpoint,
                self.oidc_token_endpoint,
                self.oidc_jwks_uri,
                self.oidc_redirect_uri,
            )
            if any(value.startswith("http://") for value in external_urls):
                raise ValueError("Non-local OIDC configuration requires HTTPS")


@lru_cache(maxsize=1)
def get_settings() -> PortalApiSettings:
    """Load and cache process configuration."""
    return PortalApiSettings()


def clear_settings_cache() -> None:
    """Clear cached settings for tests and controlled reloads."""
    get_settings.cache_clear()
