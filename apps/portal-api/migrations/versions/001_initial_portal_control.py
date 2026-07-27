"""Create the governed Portal security control schema."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_portal_control"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
VERSION = "001_initial_portal_control"


def _utc_timestamp(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("CURRENT_TIMESTAMP") if not nullable else None,
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS portal_control AUTHORIZATION portal_migration")

    op.create_table(
        "schema_migrations",
        sa.Column("version", sa.String(128), primary_key=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        _utc_timestamp("applied_at"),
        sa.Column(
            "applied_by",
            sa.String(128),
            nullable=False,
            server_default=sa.text("CURRENT_USER"),
        ),
        sa.Column("application_compat", sa.String(128), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_principals",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject_reference", sa.String(512), nullable=False),
        sa.Column(
            "display_attributes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        _utc_timestamp("created_at"),
        _utc_timestamp("updated_at"),
        sa.UniqueConstraint(
            "issuer",
            "subject_reference",
            name="uq_portal_principals_issuer_subject_reference",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_portal_principals_principal_status",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "oidc_login_transactions",
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce_hash", sa.LargeBinary(), nullable=False),
        sa.Column("browser_binding_hash", sa.LargeBinary(), nullable=False),
        sa.Column("browser_binding_key_version", sa.String(64), nullable=False),
        sa.Column("pkce_verifier_encrypted", postgresql.JSONB, nullable=False),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("return_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("claimed_by", postgresql.UUID(as_uuid=True), nullable=True),
        _utc_timestamp("claimed_at", nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _utc_timestamp("consumed_at", nullable=True),
        _utc_timestamp("created_at"),
        _utc_timestamp("updated_at"),
        sa.UniqueConstraint(
            "state_hash",
            name="uq_oidc_login_transactions_state_hash",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'CONSUMED', 'EXPIRED', 'INVALIDATED')",
            name="ck_oidc_login_transactions_login_transaction_status",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_oidc_login_transactions_login_transaction_version",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_oidc_login_transactions_eligible",
        "oidc_login_transactions",
        ["expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('PENDING', 'CLAIMED')"),
    )
    op.create_table(
        "portal_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_lookup_hash", sa.LargeBinary(), nullable=False),
        sa.Column("lookup_key_version", sa.String(64), nullable=False),
        sa.Column("session_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predecessor_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject_reference", sa.String(512), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("security_epoch", sa.BigInteger(), nullable=False),
        sa.Column("roles_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("environments_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("mapping_revision", sa.String(128), nullable=False),
        sa.Column("policy_revision", sa.String(128), nullable=False),
        sa.Column("capability_revision", sa.String(128), nullable=False),
        sa.Column("authentication_assurance", sa.String(64), nullable=False),
        _utc_timestamp("created_at"),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        _utc_timestamp("provider_expires_at", nullable=True),
        sa.Column("identity_verified_until", sa.DateTime(timezone=True), nullable=False),
        _utc_timestamp("revoked_at", nullable=True),
        sa.Column("revoked_reason", sa.String(128), nullable=True),
        _utc_timestamp("last_refresh_at", nullable=True),
        sa.Column(
            "client_signal_classification",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("audit_correlation_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["predecessor_session_id"],
            [f"{SCHEMA}.portal_sessions.session_id"],
            name="fk_portal_sessions_predecessor_session_id_portal_sessions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            [f"{SCHEMA}.portal_principals.principal_id"],
            name="fk_portal_sessions_principal_id_portal_principals",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "session_lookup_hash",
            name="uq_portal_sessions_session_lookup_hash",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REFRESH_REQUIRED', 'EXPIRED_IDLE', "
            "'EXPIRED_ABSOLUTE', 'REVOKED', 'PROVIDER_REVOKED', 'INVALID', 'TERMINATED')",
            name="ck_portal_sessions_session_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_portal_sessions_session_version"),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="ck_portal_sessions_session_idle_before_absolute",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_portal_sessions_active_principal",
        "portal_sessions",
        ["principal_id", "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('ACTIVE', 'REFRESH_REQUIRED')"),
    )
    op.create_index(
        "ix_portal_sessions_expiry",
        "portal_sessions",
        ["idle_expires_at", "absolute_expires_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "portal_token_envelopes",
        sa.Column("envelope_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("authentication_tag", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_data_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("kms_key_id", sa.String(512), nullable=False),
        sa.Column("token_generation", sa.Integer(), nullable=False),
        _utc_timestamp("created_at"),
        _utc_timestamp("rotated_at", nullable=True),
        _utc_timestamp("disposed_at", nullable=True),
        sa.UniqueConstraint(
            "session_family_id",
            name="uq_portal_token_envelopes_session_family_id",
        ),
        sa.CheckConstraint(
            "token_generation > 0",
            name="ck_portal_token_envelopes_token_generation",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_security_epochs",
        sa.Column("environment_id", sa.String(64), primary_key=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("epoch > 0", name="ck_portal_security_epochs_security_epoch"),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_policy_revisions",
        sa.Column("policy_revision", sa.String(128), primary_key=True),
        sa.Column("policy_digest", sa.String(128), nullable=False),
        sa.Column("artifact_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _utc_timestamp("activated_at", nullable=True),
        _utc_timestamp("retired_at", nullable=True),
        _utc_timestamp("created_at"),
        sa.UniqueConstraint(
            "policy_digest",
            name="uq_portal_policy_revisions_policy_digest",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'RETIRED', 'INVALID')",
            name="ck_portal_policy_revisions_policy_status",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_capability_definitions",
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.String(128), nullable=False),
        sa.Column("contract_version", sa.String(128), nullable=False),
        sa.Column("implementation_version", sa.String(128), nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _utc_timestamp("created_at"),
        sa.UniqueConstraint(
            "capability_id",
            "environment_id",
            "tenant_id",
            "revision",
            name="uq_portal_capability_definitions_identity",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_capability_overrides",
        sa.Column("override_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("override_revision", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(64), nullable=False),
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("desired_state", sa.String(32), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        _utc_timestamp("effective_until", nullable=True),
        sa.Column("actor_reference", sa.String(128), nullable=False),
        sa.Column("evidence_reference", sa.String(256), nullable=False),
        _utc_timestamp("created_at"),
        sa.UniqueConstraint(
            "override_revision",
            name="uq_portal_capability_overrides_override_revision",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "security_audit_events",
        sa.Column(
            "ledger_sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deduplication_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _utc_timestamp("recorded_at"),
        sa.Column("actor_type", sa.String(64), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issuer_id", sa.String(512), nullable=True),
        sa.Column("subject_reference", sa.String(512), nullable=True),
        sa.Column("session_reference", sa.String(256), nullable=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("environment_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(128), nullable=True),
        sa.Column("resource_type", sa.String(128), nullable=True),
        sa.Column("resource_reference", sa.String(256), nullable=True),
        sa.Column("capability_id", sa.String(128), nullable=True),
        sa.Column("decision", sa.String(64), nullable=True),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("policy_revision", sa.String(128), nullable=True),
        sa.Column("capability_revision", sa.String(128), nullable=True),
        sa.Column("authentication_assurance", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("source_network_classification", sa.String(64), nullable=True),
        sa.Column("user_agent_classification", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "integrity_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "event_id",
            name="uq_security_audit_events_event_id",
        ),
        sa.UniqueConstraint(
            "deduplication_key",
            name="uq_security_audit_events_deduplication_key",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "audit_archive_outbox",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        _utc_timestamp("lease_expires_at", nullable=True),
        _utc_timestamp("next_attempt_at"),
        sa.Column("archive_reference", sa.String(512), nullable=True),
        sa.Column("archive_checksum", sa.String(128), nullable=True),
        _utc_timestamp("created_at"),
        _utc_timestamp("delivered_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            [f"{SCHEMA}.security_audit_events.event_id"],
            name="fk_audit_archive_outbox_event_id_security_audit_events",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_audit_archive_outbox_outbox_attempt_count",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_archive_outbox_pending",
        "audit_archive_outbox",
        ["next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state IN ('PENDING', 'RETRY')"),
    )

    op.execute(
        """
        CREATE FUNCTION portal_control.reject_security_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, portal_control
        AS $$
        BEGIN
            RAISE EXCEPTION 'security audit events are append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER security_audit_events_append_only
        BEFORE UPDATE OR DELETE ON portal_control.security_audit_events
        FOR EACH ROW EXECUTE FUNCTION portal_control.reject_security_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION portal_control.current_schema_compatibility()
        RETURNS TABLE(version text, application_compat text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, portal_control
        AS $$
          SELECT sm.version::text, sm.application_compat::text
          FROM portal_control.schema_migrations AS sm
          ORDER BY sm.applied_at DESC, sm.version DESC
          LIMIT 1
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION portal_control.append_security_audit_event(event_payload jsonb)
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, portal_control
        AS $$
        DECLARE
          inserted_sequence bigint;
          inserted_event_id uuid;
        BEGIN
          IF jsonb_typeof(event_payload) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'audit event payload must be an object' USING ERRCODE = '22023';
          END IF;

          inserted_event_id := (event_payload ->> 'event_id')::uuid;
          INSERT INTO portal_control.security_audit_events (
            event_id, deduplication_key, event_type, occurred_at, actor_type,
            principal_id, issuer_id, subject_reference, session_reference,
            tenant_id, environment_id, action, resource_type, resource_reference,
            capability_id, decision, reason_code, policy_revision, capability_revision,
            authentication_assurance, correlation_id, request_id,
            source_network_classification, user_agent_classification, outcome, safe_metadata
          ) VALUES (
            inserted_event_id,
            (event_payload ->> 'deduplication_key')::uuid,
            event_payload ->> 'event_type',
            (event_payload ->> 'occurred_at')::timestamptz,
            event_payload ->> 'actor_type',
            NULLIF(event_payload ->> 'principal_id', '')::uuid,
            NULLIF(event_payload ->> 'issuer_id', ''),
            NULLIF(event_payload ->> 'subject_reference', ''),
            NULLIF(event_payload ->> 'session_reference', ''),
            NULLIF(event_payload ->> 'tenant_id', ''),
            NULLIF(event_payload ->> 'environment_id', ''),
            NULLIF(event_payload ->> 'action', ''),
            NULLIF(event_payload ->> 'resource_type', ''),
            NULLIF(event_payload ->> 'resource_reference', ''),
            NULLIF(event_payload ->> 'capability_id', ''),
            NULLIF(event_payload ->> 'decision', ''),
            NULLIF(event_payload ->> 'reason_code', ''),
            NULLIF(event_payload ->> 'policy_revision', ''),
            NULLIF(event_payload ->> 'capability_revision', ''),
            NULLIF(event_payload ->> 'authentication_assurance', ''),
            event_payload ->> 'correlation_id',
            event_payload ->> 'request_id',
            NULLIF(event_payload ->> 'source_network_classification', ''),
            NULLIF(event_payload ->> 'user_agent_classification', ''),
            event_payload ->> 'outcome',
            COALESCE(event_payload -> 'safe_metadata', '{}'::jsonb)
          )
          RETURNING ledger_sequence INTO inserted_sequence;

          INSERT INTO portal_control.audit_archive_outbox (
            event_id, publication_state, attempt_count
          ) VALUES (
            inserted_event_id, 'PENDING', 0
          );
          RETURN inserted_sequence;
        END;
        $$
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION portal_control.current_schema_compatibility() FROM PUBLIC;
        REVOKE ALL ON FUNCTION portal_control.append_security_audit_event(jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION portal_control.current_schema_compatibility() TO portal_runtime;
        GRANT EXECUTE ON FUNCTION portal_control.append_security_audit_event(jsonb)
          TO portal_runtime;
        GRANT USAGE ON SCHEMA portal_control TO portal_runtime, portal_audit, portal_archive;

        GRANT SELECT ON portal_control.portal_principals,
          portal_control.oidc_login_transactions,
          portal_control.portal_sessions,
          portal_control.portal_security_epochs,
          portal_control.portal_policy_revisions,
          portal_control.portal_capability_definitions,
          portal_control.portal_capability_overrides
          TO portal_runtime;
        GRANT INSERT ON portal_control.oidc_login_transactions,
          portal_control.portal_sessions,
          portal_control.portal_token_envelopes
          TO portal_runtime;
        GRANT UPDATE ON portal_control.oidc_login_transactions,
          portal_control.portal_sessions,
          portal_control.portal_token_envelopes
          TO portal_runtime;
        GRANT SELECT ON portal_control.security_audit_events TO portal_audit;
        GRANT SELECT ON portal_control.security_audit_events,
          portal_control.audit_archive_outbox TO portal_archive;
        GRANT UPDATE ON portal_control.audit_archive_outbox TO portal_archive;
        """
    )

    script_path = Path(__file__).resolve()
    op.execute(
        sa.text(
            """
            INSERT INTO portal_control.schema_migrations
              (version, checksum, application_compat)
            VALUES (:version, :checksum, :application_compat)
            """
        ).bindparams(
            version=VERSION,
            checksum=migration_checksum(script_path),
            application_compat=">=0.1.0,<1.0.0",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM portal_control.schema_migrations WHERE version = :version").bindparams(
            version=VERSION
        )
    )
    op.execute("DROP FUNCTION IF EXISTS portal_control.current_schema_compatibility()")
    op.execute("DROP FUNCTION IF EXISTS portal_control.append_security_audit_event(jsonb)")
    op.drop_index(
        "ix_audit_archive_outbox_pending",
        table_name="audit_archive_outbox",
        schema=SCHEMA,
    )
    op.drop_table("audit_archive_outbox", schema=SCHEMA)
    op.execute(
        "DROP TRIGGER IF EXISTS security_audit_events_append_only "
        "ON portal_control.security_audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS portal_control.reject_security_audit_mutation()")
    op.drop_table("security_audit_events", schema=SCHEMA)
    op.drop_table("portal_capability_overrides", schema=SCHEMA)
    op.drop_table("portal_capability_definitions", schema=SCHEMA)
    op.drop_table("portal_policy_revisions", schema=SCHEMA)
    op.drop_table("portal_security_epochs", schema=SCHEMA)
    op.drop_table("portal_token_envelopes", schema=SCHEMA)
    op.drop_index(
        "ix_portal_sessions_expiry",
        table_name="portal_sessions",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_portal_sessions_active_principal",
        table_name="portal_sessions",
        schema=SCHEMA,
    )
    op.drop_table("portal_sessions", schema=SCHEMA)
    op.drop_index(
        "ix_oidc_login_transactions_eligible",
        table_name="oidc_login_transactions",
        schema=SCHEMA,
    )
    op.drop_table("oidc_login_transactions", schema=SCHEMA)
    op.drop_table("portal_principals", schema=SCHEMA)
    op.drop_table("schema_migrations", schema=SCHEMA)
    op.execute("DROP SCHEMA portal_control")
