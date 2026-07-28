"""Add reliable audit delivery authority and bounded maintenance checkpoints."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum
from sqlalchemy.dialects import postgresql

revision: str = "008_audit_outbox_and_maintenance"
down_revision: str | None = "007_provider_session_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
OUTBOX = "audit_archive_outbox"
VERSION = revision


def _replace_append_function(*, local_only_supported: bool) -> None:
    outbox_insert = (
        """
          IF NOT COALESCE(
            (event_payload -> 'safe_metadata' ->> 'local_only')::boolean,
            false
          ) THEN
            INSERT INTO portal_control.audit_archive_outbox (
              outbox_id, event_id, event_type, payload_version, payload,
              destination, publication_state, attempt_count, max_attempts
            ) VALUES (
              inserted_event_id,
              inserted_event_id,
              event_payload ->> 'event_type',
              1,
              jsonb_build_object(
                'schema_version', 1,
                'event_id', event_payload ->> 'event_id',
                'event_type', event_payload ->> 'event_type',
                'occurred_at', event_payload ->> 'occurred_at',
                'correlation_id', event_payload ->> 'correlation_id',
                'actor', jsonb_build_object('kind', event_payload ->> 'actor_type'),
                'resource', jsonb_build_object(
                  'kind', NULLIF(event_payload ->> 'resource_type', '')
                ),
                'outcome', event_payload ->> 'outcome',
                'reason_code', NULLIF(event_payload ->> 'reason_code', ''),
                'attributes', COALESCE(event_payload -> 'safe_metadata', '{}'::jsonb)
                  - 'local_only'
              ),
              'local_postgres',
              'PENDING',
              0,
              5
            );
          END IF;
        """
        if local_only_supported
        else """
          INSERT INTO portal_control.audit_archive_outbox (
            event_id, publication_state, attempt_count
          ) VALUES (
            inserted_event_id, 'PENDING', 0
          );
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION portal_control.append_security_audit_event(
          event_payload jsonb
        )
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
            COALESCE(event_payload -> 'safe_metadata', '{{}}'::jsonb)
          )
          RETURNING ledger_sequence INTO inserted_sequence;

          {outbox_insert}
          RETURN inserted_sequence;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.drop_index("ix_audit_archive_outbox_pending", table_name=OUTBOX, schema=SCHEMA)
    op.add_column(
        OUTBOX,
        sa.Column("outbox_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(OUTBOX, sa.Column("event_type", sa.String(128), nullable=True), schema=SCHEMA)
    op.add_column(
        OUTBOX,
        sa.Column("payload_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column(
            "destination",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'local_postgres'"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("last_error_class", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("requeue_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        OUTBOX,
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE portal_control.audit_archive_outbox AS outbox
        SET
          outbox_id = outbox.event_id,
          event_type = audit.event_type,
          payload = jsonb_build_object(
            'schema_version', 1,
            'event_id', audit.event_id::text,
            'event_type', audit.event_type,
            'occurred_at', audit.occurred_at::text,
            'correlation_id', audit.correlation_id,
            'actor', jsonb_build_object('kind', audit.actor_type),
            'resource', jsonb_build_object('kind', audit.resource_type),
            'outcome', audit.outcome,
            'reason_code', audit.reason_code,
            'attributes', audit.safe_metadata
          ),
          publication_state = CASE
            WHEN publication_state = 'RETRY' THEN 'RETRY_SCHEDULED'
            ELSE publication_state
          END,
          updated_at = CURRENT_TIMESTAMP
        FROM portal_control.security_audit_events AS audit
        WHERE audit.event_id = outbox.event_id
        """
    )
    op.alter_column(OUTBOX, "outbox_id", nullable=False, schema=SCHEMA)
    op.alter_column(OUTBOX, "event_type", nullable=False, schema=SCHEMA)
    op.alter_column(OUTBOX, "payload", nullable=False, schema=SCHEMA)
    op.execute(
        "ALTER TABLE portal_control.audit_archive_outbox DROP CONSTRAINT pk_audit_archive_outbox"
    )
    op.create_primary_key("pk_audit_archive_outbox", OUTBOX, ["outbox_id"], schema=SCHEMA)
    op.create_unique_constraint(
        "uq_audit_archive_outbox_event_destination",
        OUTBOX,
        ["event_id", "destination"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_audit_archive_outbox_outbox_publication_state",
        OUTBOX,
        "publication_state IN ('PENDING', 'LEASED', 'RETRY_SCHEDULED', "
        "'DELIVERED', 'DEAD_LETTERED', 'CANCELLED')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_audit_archive_outbox_outbox_max_attempts",
        OUTBOX,
        "max_attempts BETWEEN 1 AND 25",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_audit_archive_outbox_outbox_requeue_count",
        OUTBOX,
        "requeue_count BETWEEN 0 AND 10",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_archive_outbox_pending",
        OUTBOX,
        ["publication_state", "next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state IN ('PENDING', 'RETRY_SCHEDULED')"),
    )
    op.create_index(
        "ix_audit_archive_outbox_lease_expiry",
        OUTBOX,
        ["lease_expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state = 'LEASED'"),
    )
    op.create_index(
        "ix_audit_archive_outbox_delivered",
        OUTBOX,
        ["delivered_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state = 'DELIVERED'"),
    )
    op.create_index(
        "ix_audit_archive_outbox_dead_lettered",
        OUTBOX,
        ["dead_lettered_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state = 'DEAD_LETTERED'"),
    )
    op.create_table(
        "audit_delivery_receipts",
        sa.Column("idempotency_key", sa.String(64), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.String(64), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "portal_maintenance_jobs",
        sa.Column("job_name", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_rows_processed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error_class", sa.String(64), nullable=True),
        sa.Column("next_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('IDLE', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_portal_maintenance_jobs_maintenance_job_status",
        ),
        sa.CheckConstraint(
            "last_rows_processed >= 0",
            name="ck_portal_maintenance_jobs_maintenance_rows_processed",
        ),
        schema=SCHEMA,
    )
    _replace_append_function(local_only_supported=True)
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION portal_control.append_security_audit_event(jsonb)
          TO portal_archive;
        GRANT EXECUTE ON FUNCTION portal_control.current_schema_compatibility()
          TO portal_archive;
        GRANT SELECT, UPDATE, DELETE ON portal_control.audit_archive_outbox
          TO portal_archive;
        GRANT SELECT, INSERT ON portal_control.audit_delivery_receipts
          TO portal_archive;
        GRANT SELECT, INSERT, UPDATE ON portal_control.portal_maintenance_jobs
          TO portal_archive;
        GRANT SELECT, DELETE ON portal_control.portal_provider_logout_receipts
          TO portal_archive;
        GRANT SELECT, DELETE ON portal_control.portal_token_envelopes
          TO portal_archive;
        GRANT SELECT ON portal_control.portal_sessions TO portal_archive;
        """
    )
    op.execute(
        sa.text(
            """
            INSERT INTO portal_control.schema_migrations
              (version, checksum, application_compat)
            VALUES (:version, :checksum, :application_compat)
            """
        ).bindparams(
            version=VERSION,
            checksum=migration_checksum(Path(__file__).resolve()),
            application_compat=">=0.1.0,<1.0.0",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM portal_control.schema_migrations WHERE version = :version").bindparams(
            version=VERSION
        )
    )
    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION portal_control.append_security_audit_event(jsonb)
          FROM portal_archive;
        REVOKE EXECUTE ON FUNCTION portal_control.current_schema_compatibility()
          FROM portal_archive;
        REVOKE SELECT, UPDATE, DELETE ON portal_control.audit_archive_outbox
          FROM portal_archive;
        REVOKE SELECT, INSERT ON portal_control.audit_delivery_receipts
          FROM portal_archive;
        REVOKE SELECT, INSERT, UPDATE ON portal_control.portal_maintenance_jobs
          FROM portal_archive;
        REVOKE SELECT, DELETE ON portal_control.portal_provider_logout_receipts
          FROM portal_archive;
        REVOKE SELECT, DELETE ON portal_control.portal_token_envelopes
          FROM portal_archive;
        REVOKE SELECT ON portal_control.portal_sessions FROM portal_archive;
        """
    )
    _replace_append_function(local_only_supported=False)
    op.drop_table("portal_maintenance_jobs", schema=SCHEMA)
    op.drop_table("audit_delivery_receipts", schema=SCHEMA)
    for index_name in (
        "ix_audit_archive_outbox_dead_lettered",
        "ix_audit_archive_outbox_delivered",
        "ix_audit_archive_outbox_lease_expiry",
        "ix_audit_archive_outbox_pending",
    ):
        op.drop_index(index_name, table_name=OUTBOX, schema=SCHEMA)
    # Alembic's naming convention can truncate generated PostgreSQL constraint
    # names. Drop the three v2-only checks by their definitions so downgrade
    # remains portable across PostgreSQL identifier-length settings.
    op.execute(
        """
        DO $$
        DECLARE
          constraint_record record;
        BEGIN
          FOR constraint_record IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'portal_control.audit_archive_outbox'::regclass
              AND contype = 'c'
              AND (
                pg_get_constraintdef(oid) LIKE '%publication_state%DEAD_LETTERED%'
                OR pg_get_constraintdef(oid) LIKE '%max_attempts%'
                OR pg_get_constraintdef(oid) LIKE '%requeue_count%'
              )
          LOOP
            EXECUTE format(
              'ALTER TABLE portal_control.audit_archive_outbox DROP CONSTRAINT %I',
              constraint_record.conname
            );
          END LOOP;
        END
        $$;
        """
    )
    op.drop_constraint(
        "uq_audit_archive_outbox_event_destination",
        OUTBOX,
        schema=SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "pk_audit_archive_outbox",
        OUTBOX,
        schema=SCHEMA,
        type_="primary",
    )
    op.create_primary_key("pk_audit_archive_outbox", OUTBOX, ["event_id"], schema=SCHEMA)
    op.execute(
        "UPDATE portal_control.audit_archive_outbox "
        "SET publication_state = 'RETRY' WHERE publication_state = 'RETRY_SCHEDULED'"
    )
    for column_name in (
        "dead_lettered_at",
        "updated_at",
        "requeue_count",
        "last_error_class",
        "last_attempt_at",
        "lease_token",
        "max_attempts",
        "destination",
        "payload",
        "payload_version",
        "event_type",
        "outbox_id",
    ):
        op.drop_column(OUTBOX, column_name, schema=SCHEMA)
    op.create_index(
        "ix_audit_archive_outbox_pending",
        OUTBOX,
        ["next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("publication_state IN ('PENDING', 'RETRY')"),
    )
