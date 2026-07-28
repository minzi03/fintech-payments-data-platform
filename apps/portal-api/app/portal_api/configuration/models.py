"""Immutable, role-scoped Portal runtime configuration models.

The flat ``PortalApiSettings`` loader remains a compatibility boundary for the
existing environment aliases.  Runtime composition uses the models in this
module so secret values and unrelated process configuration do not flow
through every process.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, SecretStr

from portal_api.abuse.client_address import ForwardedHeaderMode


class FrozenConfiguration(BaseModel):
    """Base class for immutable startup configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class PortalProcessRole(StrEnum):
    """Supported Portal process composition roots."""

    API = "api"
    AUDIT_WORKER = "audit-worker"
    MIGRATION = "migration"


class ReleaseConfiguration(FrozenConfiguration):
    environment: str
    service_name: str
    service_version: str
    api_version: str
    contract_version: str
    documentation_version: str
    build_sha: str
    build_time: str


class LoggingConfiguration(FrozenConfiguration):
    level: str
    format: str


class HttpServerConfiguration(FrozenConfiguration):
    host: str
    port: int
    allowed_origins: tuple[str, ...]
    trusted_hosts: tuple[str, ...]
    openapi_enabled: bool
    development_identity_enabled: bool


class HealthConfiguration(FrozenConfiguration):
    dependency_timeout_seconds: float
    readiness_timeout_seconds: float
    cache_ttl_seconds: float


class TelemetryConfiguration(FrozenConfiguration):
    enabled: bool
    metrics_exporter: str
    trace_exporter: str
    trace_sampling_ratio: float
    export_interval_seconds: float
    export_timeout_seconds: float
    otlp_endpoint: str
    prometheus_host: str
    prometheus_port: int
    resource_attributes: tuple[tuple[str, str], ...]


class SecurityRuntimeConfiguration(FrozenConfiguration):
    enabled: bool
    secret_provider_id: str


class OidcConfiguration(FrozenConfiguration):
    provider_id: str
    issuer: str
    discovery_url: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    allowed_algorithms: tuple[str, ...]
    group_claim_path: str
    allowed_roles: tuple[str, ...]
    allowed_environment_ids: tuple[str, ...]
    portal_tenant_id: str
    identity_mapping_revision: str
    callback_policy_revision: str
    capability_revision: str
    http_timeout_seconds: float
    cache_ttl_seconds: int
    stale_ceiling_seconds: int


class SessionPolicyConfiguration(FrozenConfiguration):
    security_epoch: int
    idle_ttl_seconds: int
    absolute_ttl_seconds: int
    identity_freshness_seconds: int
    activity_write_interval_seconds: int
    maximum_active_sessions: int


class LoginPolicyConfiguration(FrozenConfiguration):
    allowed_return_paths: tuple[str, ...]
    intent_ttl_seconds: int
    transaction_ttl_seconds: int


class ProviderLifecycleConfiguration(FrozenConfiguration):
    refresh_enabled: bool
    refresh_threshold_seconds: int
    refresh_scan_interval_seconds: float
    refresh_retry_budget: int
    refresh_initial_backoff_seconds: float
    refresh_max_backoff_seconds: float
    refresh_lease_seconds: int
    refresh_batch_size: int
    logout_timeout_seconds: float
    logout_replay_ttl_seconds: int


class AbuseConfiguration(FrozenConfiguration):
    enabled: bool
    trusted_proxy_cidrs: tuple[str, ...]
    forwarded_header_mode: ForwardedHeaderMode
    max_forwarded_hops: int
    ipv4_prefix_length: int
    ipv6_prefix_length: int
    redis_connect_timeout_seconds: float
    redis_operation_timeout_seconds: float
    redis_max_connections: int
    redis_key_prefix: str
    policy_version: str
    local_fallback_max_keys: int
    provider_max_concurrency: int
    provider_concurrency_lease_seconds: float
    backend_audit_interval_seconds: float


class AuditConfiguration(FrozenConfiguration):
    enabled: bool
    poll_interval_seconds: float
    batch_size: int
    worker_concurrency: int
    lease_seconds: float
    max_attempts: int
    base_backoff_seconds: float
    max_backoff_seconds: float
    destination: str
    delivery_timeout_seconds: float
    outbox_retention_days: int
    dead_letter_retention_days: int
    maintenance_enabled: bool
    maintenance_interval_seconds: float
    maintenance_batch_size: int
    maintenance_max_runtime_seconds: float
    replay_retention_buffer_seconds: int
    terminal_envelope_retention_days: int


class KeyRotationConfiguration(FrozenConfiguration):
    current_version: str
    previous_version: str | None
    staged_future_version: str | None
    transition_started_at: datetime | None
    transition_expires_at: datetime | None


class SecretReferenceConfiguration(FrozenConfiguration):
    """Non-secret provider and logical-reference metadata."""

    provider_id: str
    required_identities: tuple[str, ...]
    current_key_version: str
    previous_key_version: str | None
    staged_future_key_version: str | None


class EnvironmentSecretInputs(FrozenConfiguration):
    """Raw compatibility inputs consumed only by EnvironmentSecretProvider."""

    database_url: SecretStr | None = None
    audit_worker_database_url: SecretStr | None = None
    oidc_client_secret: SecretStr | None = None
    client_address_hmac_secret: SecretStr | None = None
    redis_url: SecretStr | None = None
    security_master_key: SecretStr | None = None
    security_previous_master_key: SecretStr | None = None

    @property
    def configured_names(self) -> tuple[str, ...]:
        """Return safe field names only; never expose values."""
        return tuple(name for name in type(self).model_fields if getattr(self, name) is not None)


class ApiRuntimeConfiguration(FrozenConfiguration):
    role: PortalProcessRole = PortalProcessRole.API
    release: ReleaseConfiguration
    logging: LoggingConfiguration
    server: HttpServerConfiguration
    health: HealthConfiguration
    telemetry: TelemetryConfiguration
    security: SecurityRuntimeConfiguration
    oidc: OidcConfiguration
    sessions: SessionPolicyConfiguration
    login: LoginPolicyConfiguration
    provider_lifecycle: ProviderLifecycleConfiguration
    abuse: AbuseConfiguration
    key_rotation: KeyRotationConfiguration
    secret_references: SecretReferenceConfiguration


class AuditWorkerRuntimeConfiguration(FrozenConfiguration):
    role: PortalProcessRole = PortalProcessRole.AUDIT_WORKER
    release: ReleaseConfiguration
    logging: LoggingConfiguration
    telemetry: TelemetryConfiguration
    audit: AuditConfiguration
    secret_references: SecretReferenceConfiguration


class MigrationRuntimeConfiguration(FrozenConfiguration):
    role: PortalProcessRole = PortalProcessRole.MIGRATION
    database_url_variable: str = "PORTAL_MIGRATION_DATABASE_URL"
    dialect: str = "postgresql+psycopg"
    offline_migrations_allowed: bool = False


PortalRoleConfiguration = (
    ApiRuntimeConfiguration | AuditWorkerRuntimeConfiguration | MigrationRuntimeConfiguration
)
