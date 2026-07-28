"""Typed, environment-backed Portal API configuration."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
import warnings
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from portal_api.abuse.client_address import ForwardedHeaderMode
from portal_api.configuration import (
    AbuseConfiguration,
    ApiRuntimeConfiguration,
    AuditConfiguration,
    AuditWorkerRuntimeConfiguration,
    ConfigurationDiagnostic,
    EnvironmentSecretInputs,
    HealthConfiguration,
    HttpServerConfiguration,
    KeyRotationConfiguration,
    LoggingConfiguration,
    LoginPolicyConfiguration,
    MigrationRuntimeConfiguration,
    OidcConfiguration,
    PortalConfigurationError,
    PortalConfigurationWarning,
    PortalProcessRole,
    PortalRoleConfiguration,
    ProviderLifecycleConfiguration,
    ReleaseConfiguration,
    SecretReferenceConfiguration,
    SecurityRuntimeConfiguration,
    SessionPolicyConfiguration,
    TelemetryConfiguration,
    supported_environment_aliases,
    unknown_prefixed_environment_names,
)


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
        frozen=True,
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
    secret_provider: str = "environment"
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
    security_future_key_version: str | None = None
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

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self._validate_environment_surface()

    @classmethod
    def supported_environment_aliases(cls) -> frozenset[str]:
        """Return the machine-tested flat compatibility surface."""
        return supported_environment_aliases(cls.model_fields)

    @property
    def environment_secret_inputs(self) -> EnvironmentSecretInputs:
        """Return the dedicated raw input model for the environment provider."""
        return EnvironmentSecretInputs(
            database_url=self.database_url,
            audit_worker_database_url=self.audit_worker_database_url,
            oidc_client_secret=self.oidc_client_secret,
            client_address_hmac_secret=self.client_address_hmac_secret,
            redis_url=self.redis_url,
            security_master_key=self.security_master_key,
            security_previous_master_key=self.security_previous_master_key,
        )

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
        "security_future_key_version",
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
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.secret_provider) is None:
            raise ValueError("PORTAL_API_SECRET_PROVIDER must be a bounded safe identifier")
        if (
            self.secret_provider != "environment"
            and self.environment_secret_inputs.configured_names
        ):
            names = ", ".join(
                f"PORTAL_API_{name.upper()}"
                for name in self.environment_secret_inputs.configured_names
            )
            raise ValueError(
                "Non-environment secret providers reject conflicting inline environment "
                f"secret inputs: {names}"
            )
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
            if self.database_url is None and self.secret_provider == "environment":
                raise ValueError(
                    "PORTAL_API_DATABASE_URL is required when the security runtime is enabled"
                )
            if self.database_url is not None:
                database_url = self.database_url.get_secret_value()
                if not database_url.startswith("postgresql+psycopg://"):
                    raise ValueError("PORTAL_API_DATABASE_URL must use PostgreSQL with psycopg")
            if self.environment in {PortalEnvironment.STAGING, PortalEnvironment.PRODUCTION}:
                raise ValueError(
                    "Portal security runtime is authorized only for local/development environments"
                )
            self._validate_oidc_configuration()
            if (
                self.environment is not PortalEnvironment.TEST
                and self.security_master_key is None
                and self.secret_provider == "environment"
            ):
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
        if self.environment is PortalEnvironment.STAGING and any(
            origin.startswith("http://") for origin in self.allowed_origin_values
        ):
            raise ValueError("Staging CORS origins must use HTTPS")
        self._report_deferred_production_policies()
        return self

    def _validate_environment_surface(self) -> None:
        unknown = unknown_prefixed_environment_names(
            os.environ,
            supported=self.supported_environment_aliases(),
        )
        if not unknown:
            return
        diagnostics = tuple(
            ConfigurationDiagnostic(
                code="PORTAL_CONFIG_UNKNOWN_VARIABLE",
                profile=self.environment.value,
                process_role=None,
                field_path=name,
                source="environment",
                safe_reason="unsupported PORTAL_API_ variable",
            )
            for name in unknown
        )
        if self.environment in {
            PortalEnvironment.TEST,
            PortalEnvironment.STAGING,
            PortalEnvironment.PRODUCTION,
        }:
            raise PortalConfigurationError(diagnostics)
        warnings.warn(
            "; ".join(item.render() for item in diagnostics),
            PortalConfigurationWarning,
            stacklevel=2,
        )

    def _report_deferred_production_policies(self) -> None:
        if self.environment not in {
            PortalEnvironment.STAGING,
            PortalEnvironment.PRODUCTION,
        }:
            return
        deferred: list[ConfigurationDiagnostic] = []
        if self.callback_policy_revision.startswith("local-"):
            deferred.append(
                ConfigurationDiagnostic(
                    code="PORTAL_CONFIG_DEFERRED_CALLBACK_POLICY",
                    profile=self.environment.value,
                    process_role=PortalProcessRole.API,
                    field_path="callback_policy_revision",
                    source="configuration",
                    safe_reason="production callback policy remains deferred",
                )
            )
        if self.abuse_policy_version.startswith("development-"):
            deferred.append(
                ConfigurationDiagnostic(
                    code="PORTAL_CONFIG_DEFERRED_ABUSE_POLICY",
                    profile=self.environment.value,
                    process_role=PortalProcessRole.API,
                    field_path="abuse_policy_version",
                    source="configuration",
                    safe_reason="production abuse policy remains deferred",
                )
            )
        if deferred:
            warnings.warn(
                "; ".join(item.render() for item in deferred),
                PortalConfigurationWarning,
                stacklevel=2,
            )

    def _validate_key_transition(self) -> None:
        transition_values = (
            self.security_previous_master_key,
            self.security_previous_key_version,
            self.security_key_transition_started_at,
            self.security_key_transition_expires_at,
        )
        if not any(value is not None for value in transition_values):
            self._validate_future_key_version()
            return
        required_transition_values = (
            self.security_previous_key_version,
            self.security_key_transition_started_at,
            self.security_key_transition_expires_at,
        )
        if not all(value is not None for value in required_transition_values) or (
            self.secret_provider == "environment" and self.security_previous_master_key is None
        ):
            raise ValueError(
                "Previous security key configuration requires key, version, start, and expiry"
            )
        previous_key = (
            self.security_previous_master_key_bytes
            if self.security_previous_master_key is not None
            else None
        )
        previous_version = self.security_previous_key_version
        started_at = self.security_key_transition_started_at
        expires_at = self.security_key_transition_expires_at
        if previous_version is None or started_at is None or expires_at is None:
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
        if (
            previous_key is not None
            and self.security_master_key_bytes is not None
            and previous_key == self.security_master_key_bytes
        ):
            raise ValueError("Current and previous security master keys must differ")
        self._validate_future_key_version()

    def _validate_future_key_version(self) -> None:
        future_version = self.security_future_key_version
        if future_version is None:
            return
        if not self.security_runtime_enabled:
            raise ValueError("PORTAL_API_SECURITY_FUTURE_KEY_VERSION requires the security runtime")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", future_version) is None:
            raise ValueError(
                "PORTAL_API_SECURITY_FUTURE_KEY_VERSION must be a bounded safe identifier"
            )
        if future_version in {
            self.security_key_version,
            self.security_previous_key_version,
        }:
            raise ValueError("Current, previous, and staged security key versions must differ")

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
        if self.oidc_client_secret is None and self.secret_provider == "environment":
            raise ValueError(
                "PORTAL_API_OIDC_CLIENT_SECRET is required for confidential-client authentication"
            )
        if self.oidc_client_secret is not None and not self.oidc_client_secret.get_secret_value():
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
        if self.client_address_hmac_secret is None and self.secret_provider == "environment":
            raise ValueError(
                "PORTAL_API_CLIENT_ADDRESS_HMAC_SECRET is required when abuse protection is enabled"
            )
        if self.client_address_hmac_secret is not None:
            _ = self.client_address_hmac_secret_bytes
        if self.redis_url is None and self.secret_provider == "environment":
            raise ValueError("PORTAL_API_REDIS_URL is required when abuse protection is enabled")
        if self.redis_url is None:
            return
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
        if self.audit_worker_database_url is None and self.secret_provider == "environment":
            raise ValueError(
                "PORTAL_API_AUDIT_WORKER_DATABASE_URL is required when the outbox worker is enabled"
            )
        if self.audit_worker_database_url is None:
            return
        database_url = self.audit_worker_database_url.get_secret_value()
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError(
                "PORTAL_API_AUDIT_WORKER_DATABASE_URL must use PostgreSQL with psycopg"
            )

    def for_role(self, role: PortalProcessRole) -> PortalRoleConfiguration:
        """Build the immutable narrow configuration for one process role."""
        if role is PortalProcessRole.API:
            return self._api_configuration()
        if role is PortalProcessRole.AUDIT_WORKER:
            if not self.audit_outbox_enabled:
                raise PortalConfigurationError(
                    (
                        ConfigurationDiagnostic(
                            code="PORTAL_CONFIG_ROLE_REQUIRED_FIELD",
                            profile=self.environment.value,
                            process_role=role,
                            field_path="audit_outbox_enabled",
                            source="configuration",
                            safe_reason="audit worker role requires the outbox",
                        ),
                    )
                )
            return AuditWorkerRuntimeConfiguration(
                release=self._release_configuration(),
                logging=self._logging_configuration(),
                telemetry=self._telemetry_configuration(),
                audit=self._audit_configuration(),
                secret_references=self._secret_reference_configuration(
                    ("portal/audit-worker-database-url",)
                ),
            )
        if role is PortalProcessRole.MIGRATION:
            return MigrationRuntimeConfiguration()
        raise PortalConfigurationError(
            (
                ConfigurationDiagnostic(
                    code="PORTAL_CONFIG_UNKNOWN_ROLE",
                    profile=self.environment.value,
                    process_role=None,
                    field_path="process_role",
                    source="composition",
                    safe_reason="unsupported Portal process role",
                ),
            )
        )

    def _release_configuration(self) -> ReleaseConfiguration:
        return ReleaseConfiguration(
            environment=self.environment.value,
            service_name=self.service_name,
            service_version=self.service_version,
            api_version=self.api_version,
            contract_version=self.contract_version,
            documentation_version=self.documentation_version,
            build_sha=self.build_sha,
            build_time=self.build_time,
        )

    def _logging_configuration(self) -> LoggingConfiguration:
        return LoggingConfiguration(level=self.log_level, format=self.log_format)

    def _telemetry_configuration(self) -> TelemetryConfiguration:
        return TelemetryConfiguration(
            enabled=self.telemetry_enabled,
            metrics_exporter=self.telemetry_metrics_exporter.value,
            trace_exporter=self.telemetry_trace_exporter.value,
            trace_sampling_ratio=self.telemetry_trace_sampling_ratio,
            export_interval_seconds=self.telemetry_export_interval_seconds,
            export_timeout_seconds=self.telemetry_export_timeout_seconds,
            otlp_endpoint=self.telemetry_otlp_endpoint,
            prometheus_host=self.telemetry_prometheus_host,
            prometheus_port=self.telemetry_prometheus_port,
            resource_attributes=tuple(sorted(self.telemetry_resource_attribute_values.items())),
        )

    def _secret_reference_configuration(
        self,
        identities: tuple[str, ...] | None = None,
    ) -> SecretReferenceConfiguration:
        required: list[str] = list(identities or ())
        if identities is None:
            if self.security_runtime_enabled:
                required.extend(
                    (
                        "portal/runtime-database-url",
                        "portal/oidc-client-secret",
                        "portal/security-master-key",
                    )
                )
            if self.abuse_protection_enabled:
                required.extend(
                    (
                        "portal/client-address-hmac-key",
                        "portal/redis-url",
                    )
                )
            if self.security_previous_key_version is not None:
                required.append("portal/security-master-key:previous")
        return SecretReferenceConfiguration(
            provider_id=self.secret_provider,
            required_identities=tuple(sorted(required)),
            current_key_version=self.security_key_version,
            previous_key_version=self.security_previous_key_version,
            staged_future_key_version=self.security_future_key_version,
        )

    def _api_configuration(self) -> ApiRuntimeConfiguration:
        return ApiRuntimeConfiguration(
            release=self._release_configuration(),
            logging=self._logging_configuration(),
            server=HttpServerConfiguration(
                host=self.host,
                port=self.port,
                allowed_origins=self.allowed_origin_values,
                trusted_hosts=self.trusted_host_values,
                openapi_enabled=self.openapi_enabled,
                development_identity_enabled=self.development_identity_enabled,
            ),
            health=HealthConfiguration(
                dependency_timeout_seconds=self.dependency_timeout_seconds,
                readiness_timeout_seconds=self.readiness_timeout_seconds,
                cache_ttl_seconds=self.health_cache_ttl_seconds,
            ),
            telemetry=self._telemetry_configuration(),
            security=SecurityRuntimeConfiguration(
                enabled=self.security_runtime_enabled,
                secret_provider_id=self.secret_provider,
            ),
            oidc=OidcConfiguration(
                provider_id=self.oidc_provider_id,
                issuer=self.oidc_issuer,
                discovery_url=self.oidc_discovery_url_value,
                client_id=self.oidc_client_id,
                redirect_uri=self.oidc_redirect_uri,
                scopes=self.oidc_scope_values,
                allowed_algorithms=self.oidc_allowed_algorithm_values,
                group_claim_path=self.oidc_group_claim_path,
                allowed_roles=self.oidc_allowed_role_values,
                allowed_environment_ids=self.allowed_environment_id_values,
                portal_tenant_id=self.portal_tenant_id,
                identity_mapping_revision=self.identity_mapping_revision,
                callback_policy_revision=self.callback_policy_revision,
                capability_revision=self.capability_revision,
                http_timeout_seconds=self.oidc_http_timeout_seconds,
                cache_ttl_seconds=self.oidc_cache_ttl_seconds,
                stale_ceiling_seconds=self.oidc_stale_ceiling_seconds,
            ),
            sessions=SessionPolicyConfiguration(
                security_epoch=self.session_security_epoch,
                idle_ttl_seconds=self.session_idle_ttl_seconds,
                absolute_ttl_seconds=self.session_absolute_ttl_seconds,
                identity_freshness_seconds=self.identity_freshness_seconds,
                activity_write_interval_seconds=self.session_activity_write_interval_seconds,
                maximum_active_sessions=self.maximum_active_sessions,
            ),
            login=LoginPolicyConfiguration(
                allowed_return_paths=self.allowed_return_path_values,
                intent_ttl_seconds=self.login_intent_ttl_seconds,
                transaction_ttl_seconds=self.login_transaction_ttl_seconds,
            ),
            provider_lifecycle=ProviderLifecycleConfiguration(
                refresh_enabled=self.provider_refresh_enabled,
                refresh_threshold_seconds=self.provider_refresh_threshold_seconds,
                refresh_scan_interval_seconds=self.provider_refresh_scan_interval_seconds,
                refresh_retry_budget=self.provider_refresh_retry_budget,
                refresh_initial_backoff_seconds=self.provider_refresh_initial_backoff_seconds,
                refresh_max_backoff_seconds=self.provider_refresh_max_backoff_seconds,
                refresh_lease_seconds=self.provider_refresh_lease_seconds,
                refresh_batch_size=self.provider_refresh_batch_size,
                logout_timeout_seconds=self.provider_logout_timeout_seconds,
                logout_replay_ttl_seconds=self.provider_logout_replay_ttl_seconds,
            ),
            abuse=AbuseConfiguration(
                enabled=self.abuse_protection_enabled,
                trusted_proxy_cidrs=self.trusted_proxy_cidr_values,
                forwarded_header_mode=self.forwarded_header_mode,
                max_forwarded_hops=self.max_forwarded_hops,
                ipv4_prefix_length=self.ipv4_prefix_length,
                ipv6_prefix_length=self.ipv6_prefix_length,
                redis_connect_timeout_seconds=self.redis_connect_timeout_seconds,
                redis_operation_timeout_seconds=self.redis_operation_timeout_seconds,
                redis_max_connections=self.redis_max_connections,
                redis_key_prefix=self.redis_key_prefix,
                policy_version=self.abuse_policy_version,
                local_fallback_max_keys=self.abuse_local_fallback_max_keys,
                provider_max_concurrency=self.abuse_provider_max_concurrency,
                provider_concurrency_lease_seconds=(self.abuse_provider_concurrency_lease_seconds),
                backend_audit_interval_seconds=self.abuse_backend_audit_interval_seconds,
            ),
            key_rotation=KeyRotationConfiguration(
                current_version=self.security_key_version,
                previous_version=self.security_previous_key_version,
                staged_future_version=self.security_future_key_version,
                transition_started_at=self.security_key_transition_started_at,
                transition_expires_at=self.security_key_transition_expires_at,
            ),
            secret_references=self._secret_reference_configuration(),
        )

    def _audit_configuration(self) -> AuditConfiguration:
        return AuditConfiguration(
            enabled=self.audit_outbox_enabled,
            poll_interval_seconds=self.audit_outbox_poll_interval_seconds,
            batch_size=self.audit_outbox_batch_size,
            worker_concurrency=self.audit_outbox_worker_concurrency,
            lease_seconds=self.audit_outbox_lease_seconds,
            max_attempts=self.audit_outbox_max_attempts,
            base_backoff_seconds=self.audit_outbox_base_backoff_seconds,
            max_backoff_seconds=self.audit_outbox_max_backoff_seconds,
            destination=self.audit_outbox_destination,
            delivery_timeout_seconds=self.audit_outbox_delivery_timeout_seconds,
            outbox_retention_days=self.audit_outbox_retention_days,
            dead_letter_retention_days=self.audit_dead_letter_retention_days,
            maintenance_enabled=self.maintenance_enabled,
            maintenance_interval_seconds=self.maintenance_interval_seconds,
            maintenance_batch_size=self.maintenance_batch_size,
            maintenance_max_runtime_seconds=self.maintenance_max_runtime_seconds,
            replay_retention_buffer_seconds=self.replay_retention_buffer_seconds,
            terminal_envelope_retention_days=self.terminal_envelope_retention_days,
        )


@lru_cache(maxsize=1)
def get_settings() -> PortalApiSettings:
    """Load and cache process configuration."""
    return PortalApiSettings()


def clear_settings_cache() -> None:
    """Clear cached settings for tests and controlled reloads."""
    get_settings.cache_clear()
