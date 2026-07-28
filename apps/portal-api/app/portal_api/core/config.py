"""Typed, environment-backed Portal API configuration."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from portal_api.abuse.client_address import ForwardedHeaderMode


class PortalEnvironment(StrEnum):
    """Supported Portal API runtime environments."""

    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class TelemetryMetricsExporter(StrEnum):
    """Supported vendor-neutral metrics export pipelines."""

    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"
    PROMETHEUS = "prometheus"


class TelemetryTraceExporter(StrEnum):
    """Supported trace export pipelines."""

    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _decode_master_key(value: SecretStr | None, *, variable_name: str) -> bytes | None:
    if value is None:
        return None
    try:
        encoded = value.get_secret_value().encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError(f"{variable_name} must be valid base64url") from error
    if len(decoded) != 32:
        raise ValueError(f"{variable_name} must decode to 256 bits")
    return decoded


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
    telemetry_metrics_exporter: TelemetryMetricsExporter = TelemetryMetricsExporter.PROMETHEUS
    telemetry_trace_exporter: TelemetryTraceExporter = TelemetryTraceExporter.NONE
    telemetry_trace_sampling_ratio: float = Field(default=0.1, ge=0, le=1)
    telemetry_export_interval_seconds: float = Field(default=30, ge=1, le=300)
    telemetry_export_timeout_seconds: float = Field(default=10, gt=0, le=30)
    telemetry_otlp_endpoint: str = "http://localhost:4318"
    telemetry_prometheus_host: str = "127.0.0.1"
    telemetry_prometheus_port: int = Field(default=9464, ge=1024, le=65535)
    telemetry_resource_attributes: str = ""
    openapi_enabled: bool = True
    development_identity_enabled: bool = False
    security_runtime_enabled: bool = False
    database_url: SecretStr | None = None
    abuse_protection_enabled: bool = False
    trusted_proxy_cidrs: str = ""
    forwarded_header_mode: ForwardedHeaderMode = ForwardedHeaderMode.DIRECT
    max_forwarded_hops: int = Field(default=5, ge=1, le=16)
    ipv4_prefix_length: int = Field(default=24, ge=16, le=32)
    ipv6_prefix_length: int = Field(default=64, ge=32, le=128)
    client_address_hmac_secret: SecretStr | None = None
    redis_url: SecretStr | None = None
    redis_connect_timeout_seconds: float = Field(default=0.5, gt=0, le=5)
    redis_operation_timeout_seconds: float = Field(default=0.25, gt=0, le=5)
    redis_max_connections: int = Field(default=50, ge=1, le=500)
    redis_key_prefix: str = "portal:abuse"
    abuse_policy_version: str = "development-v1"
    abuse_local_fallback_max_keys: int = Field(default=10_000, ge=100, le=100_000)
    abuse_provider_max_concurrency: int = Field(default=20, ge=1, le=500)
    abuse_provider_concurrency_lease_seconds: float = Field(default=15, ge=1, le=120)
    abuse_backend_audit_interval_seconds: float = Field(default=60, ge=1, le=3_600)
    audit_outbox_enabled: bool = False
    audit_worker_database_url: SecretStr | None = None
    audit_outbox_poll_interval_seconds: float = Field(default=1, ge=0.1, le=60)
    audit_outbox_batch_size: int = Field(default=100, ge=1, le=500)
    audit_outbox_worker_concurrency: int = Field(default=4, ge=1, le=32)
    audit_outbox_lease_seconds: float = Field(default=30, ge=5, le=600)
    audit_outbox_max_attempts: int = Field(default=5, ge=1, le=25)
    audit_outbox_base_backoff_seconds: float = Field(default=1, ge=0.1, le=300)
    audit_outbox_max_backoff_seconds: float = Field(default=60, ge=1, le=3600)
    audit_outbox_destination: str = "local_postgres"
    audit_outbox_delivery_timeout_seconds: float = Field(default=5, gt=0, le=60)
    audit_outbox_retention_days: int = Field(default=7, ge=1, le=365)
    audit_dead_letter_retention_days: int = Field(default=30, ge=1, le=3650)
    maintenance_enabled: bool = True
    maintenance_interval_seconds: float = Field(default=60, ge=1, le=3600)
    maintenance_batch_size: int = Field(default=100, ge=1, le=1000)
    maintenance_max_runtime_seconds: float = Field(default=30, ge=1, le=300)
    replay_retention_buffer_seconds: int = Field(default=300, ge=0, le=86400)
    terminal_envelope_retention_days: int = Field(default=7, ge=1, le=365)
    security_master_key: SecretStr | None = None
    security_key_version: str = "local-development-v1"
    security_previous_master_key: SecretStr | None = None
    security_previous_key_version: str | None = None
    security_key_transition_started_at: datetime | None = None
    security_key_transition_expires_at: datetime | None = None
    oidc_provider_id: str = "local-keycloak"
    oidc_issuer: str = "http://portal-idp.localhost:8081/realms/fintech-portal"
    oidc_discovery_url: str | None = None
    oidc_client_id: str = "fintech-portal"
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str = "http://localhost:3000/portal-api/v1/auth/callback"
    oidc_scopes: str = "openid profile"
    oidc_allowed_algorithms: str = "RS256"
    oidc_group_claim_path: str = "groups"
    oidc_allowed_roles: str = (
        "portal_viewer,data_engineer_viewer,platform_operator_viewer,"
        "security_auditor_viewer,portal_admin_viewer"
    )
    allowed_environment_ids: str = "local,development"
    portal_tenant_id: str = "fintech-platform-primary"
    identity_mapping_revision: str = "local-mapping-v1"
    callback_policy_revision: str = "local-callback-policy-v1"
    capability_revision: str = "local-capability-v1"
    session_security_epoch: int = Field(default=1, gt=0)
    session_idle_ttl_seconds: int = Field(default=1800, gt=0, le=1800)
    session_absolute_ttl_seconds: int = Field(default=28800, gt=0, le=28800)
    identity_freshness_seconds: int = Field(default=900, gt=0, le=900)
    session_activity_write_interval_seconds: int = Field(default=60, gt=0, le=60)
    maximum_active_sessions: int = Field(default=5, gt=0, le=5)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    oidc_cache_ttl_seconds: int = Field(default=900, ge=900, le=900)
    oidc_stale_ceiling_seconds: int = Field(default=3600, ge=3600, le=3600)
    provider_refresh_enabled: bool = True
    provider_refresh_threshold_seconds: int = Field(default=120, ge=30, le=600)
    provider_refresh_scan_interval_seconds: float = Field(default=5, ge=1, le=60)
    provider_refresh_retry_budget: int = Field(default=3, ge=1, le=10)
    provider_refresh_initial_backoff_seconds: float = Field(default=1, ge=0.1, le=30)
    provider_refresh_max_backoff_seconds: float = Field(default=30, ge=1, le=300)
    provider_refresh_lease_seconds: int = Field(default=30, ge=10, le=300)
    provider_refresh_batch_size: int = Field(default=25, ge=1, le=100)
    provider_logout_timeout_seconds: float = Field(default=5, gt=0, le=30)
    provider_logout_replay_ttl_seconds: int = Field(default=86400, ge=300, le=86400)
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
    def trusted_proxy_cidr_values(self) -> tuple[str, ...]:
        return _csv_values(self.trusted_proxy_cidrs)

    @property
    def client_address_hmac_secret_bytes(self) -> bytes | None:
        return _decode_master_key(
            self.client_address_hmac_secret,
            variable_name="PORTAL_API_CLIENT_ADDRESS_HMAC_SECRET",
        )

    @property
    def is_production(self) -> bool:
        return self.environment is PortalEnvironment.PRODUCTION

    @property
    def allowed_return_path_values(self) -> tuple[str, ...]:
        return _csv_values(self.allowed_return_paths)

    @property
    def oidc_scope_values(self) -> tuple[str, ...]:
        return tuple(scope for scope in self.oidc_scopes.split() if scope)

    @property
    def oidc_discovery_url_value(self) -> str:
        return self.oidc_discovery_url or (
            f"{self.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        )

    @property
    def oidc_allowed_algorithm_values(self) -> tuple[str, ...]:
        return _csv_values(self.oidc_allowed_algorithms)

    @property
    def oidc_allowed_role_values(self) -> tuple[str, ...]:
        return _csv_values(self.oidc_allowed_roles)

    @property
    def allowed_environment_id_values(self) -> tuple[str, ...]:
        return _csv_values(self.allowed_environment_ids)

    @property
    def telemetry_resource_attribute_values(self) -> dict[str, str]:
        """Return bounded non-secret resource attributes."""
        attributes: dict[str, str] = {}
        for item in _csv_values(self.telemetry_resource_attributes):
            key, separator, value = item.partition("=")
            if (
                separator != "="
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key) is None
                or not value
                or len(value) > 128
                or any(
                    secret_word in key.casefold()
                    for secret_word in ("password", "secret", "token", "credential", "cookie")
                )
            ):
                raise ValueError(
                    "PORTAL_API_TELEMETRY_RESOURCE_ATTRIBUTES must contain bounded key=value pairs"
                )
            attributes[key] = value
        if len(attributes) > 16:
            raise ValueError("PORTAL_API_TELEMETRY_RESOURCE_ATTRIBUTES supports at most 16 entries")
        return attributes

    @property
    def security_master_key_bytes(self) -> bytes | None:
        return _decode_master_key(
            self.security_master_key,
            variable_name="PORTAL_API_SECURITY_MASTER_KEY",
        )

    @property
    def security_previous_master_key_bytes(self) -> bytes | None:
        return _decode_master_key(
            self.security_previous_master_key,
            variable_name="PORTAL_API_SECURITY_PREVIOUS_MASTER_KEY",
        )

    @field_validator(
        "client_address_hmac_secret",
        "redis_url",
        "security_previous_master_key",
        "security_previous_key_version",
        "security_key_transition_started_at",
        "security_key_transition_expires_at",
        mode="before",
    )
    @classmethod
    def empty_transition_value_is_unset(cls, value: object) -> object:
        return None if value == "" else value

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
        self._validate_abuse_configuration()
        self._validate_audit_outbox_configuration()
        if self.telemetry_enabled:
            if (
                self.telemetry_metrics_exporter is TelemetryMetricsExporter.NONE
                and self.telemetry_trace_exporter is TelemetryTraceExporter.NONE
            ):
                raise ValueError(
                    "Telemetry requires at least one metrics or trace exporter when enabled"
                )
            if (
                self.telemetry_metrics_exporter is TelemetryMetricsExporter.OTLP
                or self.telemetry_trace_exporter is TelemetryTraceExporter.OTLP
            ):
                endpoint = urlsplit(self.telemetry_otlp_endpoint)
                if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
                    raise ValueError(
                        "PORTAL_API_TELEMETRY_OTLP_ENDPOINT must be an absolute HTTP(S) URL"
                    )
                if endpoint.username is not None or endpoint.password is not None:
                    raise ValueError(
                        "PORTAL_API_TELEMETRY_OTLP_ENDPOINT must not contain credentials"
                    )
                if self.is_production and endpoint.scheme != "https":
                    raise ValueError("Production OTLP export requires HTTPS")
            if not self.telemetry_prometheus_host.strip():
                raise ValueError("PORTAL_API_TELEMETRY_PROMETHEUS_HOST must not be empty")
            _ = self.telemetry_resource_attribute_values
        self._validate_provider_lifecycle_configuration()
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
            if self.environment is not PortalEnvironment.TEST and self.security_master_key is None:
                raise ValueError(
                    "PORTAL_API_SECURITY_MASTER_KEY is required for restart-safe "
                    "local/development security runtime"
                )
            if self.security_master_key is not None:
                _ = self.security_master_key_bytes
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.security_key_version) is None:
                raise ValueError(
                    "PORTAL_API_SECURITY_KEY_VERSION must be a bounded safe identifier"
                )
            self._validate_key_transition()
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

    def _validate_key_transition(self) -> None:
        transition_values = (
            self.security_previous_master_key,
            self.security_previous_key_version,
            self.security_key_transition_started_at,
            self.security_key_transition_expires_at,
        )
        if not any(value is not None for value in transition_values):
            return
        if not all(value is not None for value in transition_values):
            raise ValueError(
                "Previous security key configuration requires key, version, start, and expiry"
            )
        previous_key = self.security_previous_master_key_bytes
        previous_version = self.security_previous_key_version
        started_at = self.security_key_transition_started_at
        expires_at = self.security_key_transition_expires_at
        if (
            previous_key is None
            or previous_version is None
            or started_at is None
            or expires_at is None
        ):
            raise ValueError("Previous security key transition is incomplete")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", previous_version) is None:
            raise ValueError(
                "PORTAL_API_SECURITY_PREVIOUS_KEY_VERSION must be a bounded safe identifier"
            )
        if previous_version == self.security_key_version:
            raise ValueError("Current and previous security key versions must differ")
        if started_at.utcoffset() is None or expires_at.utcoffset() is None:
            raise ValueError("Security key transition timestamps must include a timezone")
        transition_seconds = (expires_at - started_at).total_seconds()
        if transition_seconds <= 0:
            raise ValueError("Security key transition expiry must follow its start")
        if transition_seconds > self.session_absolute_ttl_seconds:
            raise ValueError(
                "Security key transition window cannot exceed the absolute session lifetime"
            )

    def _validate_oidc_configuration(self) -> None:
        if not self.allowed_return_path_values or any(
            not value.startswith("/") or value.startswith("//") or "\\" in value
            for value in self.allowed_return_path_values
        ):
            raise ValueError("PORTAL_API_ALLOWED_RETURN_PATHS must contain local absolute paths")
        if "openid" not in self.oidc_scope_values:
            raise ValueError("PORTAL_API_OIDC_SCOPES must include openid")
        if not self.oidc_allowed_algorithm_values:
            raise ValueError("PORTAL_API_OIDC_ALLOWED_ALGORITHMS must not be empty")
        if not self.oidc_allowed_role_values:
            raise ValueError("PORTAL_API_OIDC_ALLOWED_ROLES must not be empty")
        if not self.allowed_environment_id_values:
            raise ValueError("PORTAL_API_ALLOWED_ENVIRONMENT_IDS must not be empty")
        if self.session_idle_ttl_seconds > self.session_absolute_ttl_seconds:
            raise ValueError("Portal session idle lifetime cannot exceed absolute lifetime")
        if self.oidc_client_secret is None or not self.oidc_client_secret.get_secret_value():
            raise ValueError(
                "PORTAL_API_OIDC_CLIENT_SECRET is required for confidential-client authentication"
            )
        for field_name in (
            "oidc_issuer",
            "oidc_redirect_uri",
        ):
            value = getattr(self, field_name)
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"PORTAL_API_{field_name.upper()} must be an absolute HTTP(S) URL")
        discovery = urlsplit(self.oidc_discovery_url_value)
        if discovery.scheme not in {"http", "https"} or not discovery.netloc:
            raise ValueError("PORTAL_API_OIDC_DISCOVERY_URL must be an absolute HTTP(S) URL")
        if self.environment not in {PortalEnvironment.LOCAL, PortalEnvironment.TEST}:
            external_urls = (
                self.oidc_issuer,
                self.oidc_discovery_url_value,
                self.oidc_redirect_uri,
            )
            if any(value.startswith("http://") for value in external_urls):
                raise ValueError("Non-local OIDC configuration requires HTTPS")

    def _validate_provider_lifecycle_configuration(self) -> None:
        if (
            self.provider_refresh_initial_backoff_seconds
            > self.provider_refresh_max_backoff_seconds
        ):
            raise ValueError("Provider refresh initial backoff cannot exceed the maximum backoff")
        if self.provider_refresh_lease_seconds <= max(
            self.oidc_http_timeout_seconds,
            self.provider_logout_timeout_seconds,
        ):
            raise ValueError("Provider refresh lease must exceed the configured provider timeout")

    def _validate_abuse_configuration(self) -> None:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9:_.-]{0,63}", self.redis_key_prefix) is None:
            raise ValueError("PORTAL_API_REDIS_KEY_PREFIX must be a bounded safe identifier")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.abuse_policy_version) is None:
            raise ValueError("PORTAL_API_ABUSE_POLICY_VERSION must be a bounded safe identifier")
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for value in self.trusted_proxy_cidr_values:
            try:
                network = ipaddress.ip_network(value, strict=True)
            except ValueError as error:
                raise ValueError(
                    "PORTAL_API_TRUSTED_PROXY_CIDRS must contain valid canonical CIDRs"
                ) from error
            if network.prefixlen == 0:
                raise ValueError("Catch-all trusted proxy CIDRs are forbidden")
            networks.append(network)
        if self.forwarded_header_mode is ForwardedHeaderMode.X_FORWARDED_FOR and not networks:
            raise ValueError("Forwarded headers require at least one explicit trusted proxy CIDR")
        if not self.abuse_protection_enabled:
            return
        if self.client_address_hmac_secret is None:
            raise ValueError(
                "PORTAL_API_CLIENT_ADDRESS_HMAC_SECRET is required when abuse protection is enabled"
            )
        _ = self.client_address_hmac_secret_bytes
        if self.redis_url is None:
            raise ValueError("PORTAL_API_REDIS_URL is required when abuse protection is enabled")
        parsed = urlsplit(self.redis_url.get_secret_value())
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("PORTAL_API_REDIS_URL must be a valid redis:// or rediss:// URL")
        if parsed.query or parsed.fragment:
            raise ValueError("PORTAL_API_REDIS_URL must not contain query or fragment components")
        if self.is_production and parsed.scheme != "rediss":
            raise ValueError("Production abuse protection requires Redis TLS")

    def _validate_audit_outbox_configuration(self) -> None:
        if self.audit_outbox_base_backoff_seconds > self.audit_outbox_max_backoff_seconds:
            raise ValueError("Audit outbox base backoff cannot exceed the maximum backoff")
        if self.audit_outbox_lease_seconds <= self.audit_outbox_delivery_timeout_seconds:
            raise ValueError("Audit outbox lease must exceed the delivery timeout")
        if self.audit_outbox_destination != "local_postgres":
            raise ValueError("PORTAL_API_AUDIT_OUTBOX_DESTINATION must be local_postgres")
        if self.audit_dead_letter_retention_days < self.audit_outbox_retention_days:
            raise ValueError(
                "Dead-letter retention cannot be shorter than delivered outbox retention"
            )
        if not self.audit_outbox_enabled:
            return
        if self.audit_worker_database_url is None:
            raise ValueError(
                "PORTAL_API_AUDIT_WORKER_DATABASE_URL is required when the outbox worker is enabled"
            )
        database_url = self.audit_worker_database_url.get_secret_value()
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError(
                "PORTAL_API_AUDIT_WORKER_DATABASE_URL must use PostgreSQL with psycopg"
            )


@lru_cache(maxsize=1)
def get_settings() -> PortalApiSettings:
    """Load and cache process configuration."""
    return PortalApiSettings()


def clear_settings_cache() -> None:
    """Clear cached settings for tests and controlled reloads."""
    get_settings.cache_clear()
