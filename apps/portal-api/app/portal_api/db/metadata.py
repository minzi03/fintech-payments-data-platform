"""SQLAlchemy Core metadata for the versioned Portal security schema."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

PORTAL_SCHEMA = "portal_control"
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(schema=PORTAL_SCHEMA, naming_convention=NAMING_CONVENTION)
UTC_NOW = text("CURRENT_TIMESTAMP")

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("version", String(128), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("applied_by", String(128), nullable=False, server_default=text("CURRENT_USER")),
    Column("application_compat", String(128), nullable=False),
)

portal_principals = Table(
    "portal_principals",
    metadata,
    Column("principal_id", UUID(as_uuid=True), primary_key=True),
    Column("issuer", String(512), nullable=False),
    Column("subject_reference", String(512), nullable=False),
    Column("display_attributes", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint("issuer", "subject_reference"),
    CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="principal_status"),
)

portal_login_intents = Table(
    "portal_login_intents",
    metadata,
    Column("intent_id", UUID(as_uuid=True), primary_key=True),
    Column("intent_lookup_hash", LargeBinary, nullable=False, unique=True),
    Column("selected_provider", String(128), nullable=False),
    Column("validated_return_path", Text, nullable=True),
    Column("browser_binding_reference", String(128), nullable=True),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('PENDING', 'CONSUMED', 'EXPIRED')",
        name="login_intent_status",
    ),
    CheckConstraint("version > 0", name="login_intent_version"),
)
Index(
    "ix_portal_login_intents_pending",
    portal_login_intents.c.expires_at,
    postgresql_where=portal_login_intents.c.status == "PENDING",
)

oidc_login_transactions = Table(
    "oidc_login_transactions",
    metadata,
    Column("transaction_id", UUID(as_uuid=True), primary_key=True),
    Column("state_hash", LargeBinary, nullable=False, unique=True),
    Column("nonce_hash", LargeBinary, nullable=False),
    Column("browser_binding_hash", LargeBinary, nullable=False),
    Column("browser_binding_key_version", String(64), nullable=False),
    Column("pkce_verifier_encrypted", JSONB, nullable=False),
    Column("provider_id", String(128), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("return_path", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("claimed_by", UUID(as_uuid=True), nullable=True),
    Column("claimed_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint(
        "status IN ('PENDING', 'CLAIMED', 'CONSUMED', 'EXPIRED', 'INVALIDATED')",
        name="login_transaction_status",
    ),
    CheckConstraint("version > 0", name="login_transaction_version"),
)
Index(
    "ix_oidc_login_transactions_eligible",
    oidc_login_transactions.c.expires_at,
    postgresql_where=oidc_login_transactions.c.status.in_(("PENDING", "CLAIMED")),
)

portal_sessions = Table(
    "portal_sessions",
    metadata,
    Column("session_id", UUID(as_uuid=True), primary_key=True),
    Column("session_lookup_hash", LargeBinary, nullable=False, unique=True),
    Column("lookup_key_version", String(64), nullable=False),
    Column("session_family_id", UUID(as_uuid=True), nullable=False),
    Column(
        "predecessor_session_id",
        UUID(as_uuid=True),
        ForeignKey(f"{PORTAL_SCHEMA}.portal_sessions.session_id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column(
        "principal_id",
        UUID(as_uuid=True),
        ForeignKey(f"{PORTAL_SCHEMA}.portal_principals.principal_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("issuer", String(512), nullable=False),
    Column("subject_reference", String(512), nullable=False),
    Column("tenant_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("security_epoch", BigInteger, nullable=False),
    Column("roles_snapshot", JSONB, nullable=False),
    Column("environments_snapshot", JSONB, nullable=False),
    Column("mapping_revision", String(128), nullable=False),
    Column("policy_revision", String(128), nullable=False),
    Column("capability_revision", String(128), nullable=False),
    Column("authentication_assurance", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("authenticated_at", DateTime(timezone=True), nullable=False),
    Column("last_activity_at", DateTime(timezone=True), nullable=False),
    Column("idle_expires_at", DateTime(timezone=True), nullable=False),
    Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
    Column("provider_expires_at", DateTime(timezone=True), nullable=True),
    Column("identity_verified_until", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("revoked_reason", String(128), nullable=True),
    Column("last_refresh_at", DateTime(timezone=True), nullable=True),
    Column(
        "client_signal_classification", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    ),
    Column("audit_correlation_id", String(128), nullable=False),
    Column("csrf_token_hash", LargeBinary, nullable=False),
    Column("csrf_generation", Integer, nullable=False),
    CheckConstraint(
        "status IN ('ACTIVE', 'REFRESH_REQUIRED', 'EXPIRED_IDLE', 'EXPIRED_ABSOLUTE', "
        "'REVOKED', 'PROVIDER_REVOKED', 'INVALID', 'TERMINATED')",
        name="session_status",
    ),
    CheckConstraint("version > 0", name="session_version"),
    CheckConstraint("csrf_generation > 0", name="session_csrf_generation"),
    CheckConstraint("idle_expires_at <= absolute_expires_at", name="session_idle_before_absolute"),
)
Index(
    "ix_portal_sessions_active_principal",
    portal_sessions.c.principal_id,
    portal_sessions.c.created_at,
    postgresql_where=portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
)
Index(
    "ix_portal_sessions_expiry",
    portal_sessions.c.idle_expires_at,
    portal_sessions.c.absolute_expires_at,
)

portal_token_envelopes = Table(
    "portal_token_envelopes",
    metadata,
    Column("envelope_id", UUID(as_uuid=True), primary_key=True),
    Column("session_family_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("nonce", LargeBinary, nullable=False),
    Column("authentication_tag", LargeBinary, nullable=False),
    Column("wrapped_data_key", LargeBinary, nullable=False),
    Column("wrapped_data_key_nonce", LargeBinary, nullable=False),
    Column("kms_key_id", String(512), nullable=False),
    Column("token_generation", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("rotated_at", DateTime(timezone=True), nullable=True),
    Column("disposed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("token_generation > 0", name="token_generation"),
)

portal_security_epochs = Table(
    "portal_security_epochs",
    metadata,
    Column("environment_id", String(64), primary_key=True),
    Column("epoch", BigInteger, nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    Column("reason", String(256), nullable=False),
    Column("audit_event_id", UUID(as_uuid=True), nullable=False),
    CheckConstraint("epoch > 0", name="security_epoch"),
)

portal_policy_revisions = Table(
    "portal_policy_revisions",
    metadata,
    Column("policy_revision", String(128), primary_key=True),
    Column("policy_digest", String(128), nullable=False, unique=True),
    Column("artifact_version", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=True),
    Column("retired_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    CheckConstraint("status IN ('PENDING', 'ACTIVE', 'RETIRED', 'INVALID')", name="policy_status"),
)

portal_capability_definitions = Table(
    "portal_capability_definitions",
    metadata,
    Column("definition_id", UUID(as_uuid=True), primary_key=True),
    Column("capability_id", String(128), nullable=False),
    Column("environment_id", String(64), nullable=False),
    Column("tenant_id", String(128), nullable=False),
    Column("revision", String(128), nullable=False),
    Column("contract_version", String(128), nullable=False),
    Column("implementation_version", String(128), nullable=True),
    Column("mode", String(32), nullable=False),
    Column("state", String(32), nullable=False),
    Column("metadata_json", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    UniqueConstraint(
        "capability_id",
        "environment_id",
        "tenant_id",
        "revision",
        name="uq_portal_capability_definitions_identity",
    ),
)

portal_capability_overrides = Table(
    "portal_capability_overrides",
    metadata,
    Column("override_id", UUID(as_uuid=True), primary_key=True),
    Column("override_revision", String(128), nullable=False, unique=True),
    Column("environment_id", String(64), nullable=False),
    Column("capability_id", String(128), nullable=False),
    Column("desired_state", String(32), nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("effective_until", DateTime(timezone=True), nullable=True),
    Column("actor_reference", String(128), nullable=False),
    Column("evidence_reference", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
)

security_audit_events = Table(
    "security_audit_events",
    metadata,
    Column("ledger_sequence", BigInteger, Identity(always=True), primary_key=True),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("deduplication_key", UUID(as_uuid=True), nullable=False, unique=True),
    Column("event_type", String(128), nullable=False),
    Column("event_version", Integer, nullable=False, server_default=text("1")),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("actor_type", String(64), nullable=False),
    Column("principal_id", UUID(as_uuid=True), nullable=True),
    Column("issuer_id", String(512), nullable=True),
    Column("subject_reference", String(512), nullable=True),
    Column("session_reference", String(256), nullable=True),
    Column("tenant_id", String(128), nullable=True),
    Column("environment_id", String(64), nullable=True),
    Column("action", String(128), nullable=True),
    Column("resource_type", String(128), nullable=True),
    Column("resource_reference", String(256), nullable=True),
    Column("capability_id", String(128), nullable=True),
    Column("decision", String(64), nullable=True),
    Column("reason_code", String(128), nullable=True),
    Column("policy_revision", String(128), nullable=True),
    Column("capability_revision", String(128), nullable=True),
    Column("authentication_assurance", String(64), nullable=True),
    Column("correlation_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("source_network_classification", String(64), nullable=True),
    Column("user_agent_classification", String(64), nullable=True),
    Column("outcome", String(64), nullable=False),
    Column("safe_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("integrity_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    implicit_returning=False,
)

audit_archive_outbox = Table(
    "audit_archive_outbox",
    metadata,
    Column(
        "event_id",
        UUID(as_uuid=True),
        ForeignKey(f"{PORTAL_SCHEMA}.security_audit_events.event_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("publication_state", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("lease_owner", String(128), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("archive_reference", String(512), nullable=True),
    Column("archive_checksum", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=UTC_NOW),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("attempt_count >= 0", name="outbox_attempt_count"),
)
Index(
    "ix_audit_archive_outbox_pending",
    audit_archive_outbox.c.next_attempt_at,
    postgresql_where=audit_archive_outbox.c.publication_state.in_(("PENDING", "RETRY")),
)
