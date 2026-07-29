"""Add durable provider-token lifecycle and refresh coordination authority."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum
from sqlalchemy.dialects import postgresql

revision: str = "007_provider_session_lifecycle"
down_revision: str | None = "006_session_revocation_fence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
TABLE = "portal_token_envelopes"
VERSION = revision


def upgrade() -> None:
    op.create_table(
        "portal_provider_logout_receipts",
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("jti_hash", sa.LargeBinary(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "jti_hash",
            name="uq_portal_provider_logout_receipts_jti_hash",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_portal_provider_logout_receipts_expiry",
        "portal_provider_logout_receipts",
        ["expires_at"],
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("provider_id", sa.String(128), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("provider_subject", sa.String(512), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("provider_session", sa.String(512), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column(
            "lifecycle_state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("refresh_token_fingerprint", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("previous_refresh_token_fingerprint", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column(
            "refresh_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("refresh_lease_owner", sa.String(128), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("refresh_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("next_refresh_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("last_failure_code", sa.String(128), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema=SCHEMA,
    )

    op.execute(
        """
        UPDATE portal_control.portal_token_envelopes AS envelope
        SET provider_id = 'configured-oidc',
            provider_subject = source.subject_reference,
            issued_at = envelope.created_at,
            expires_at = source.provider_expires_at
        FROM (
          SELECT DISTINCT ON (session_family_id)
            session_family_id, subject_reference, provider_expires_at
          FROM portal_control.portal_sessions
          ORDER BY session_family_id, created_at DESC
        ) AS source
        WHERE source.session_family_id = envelope.session_family_id
        """
    )
    op.execute(
        """
        UPDATE portal_control.portal_token_envelopes
        SET provider_id = COALESCE(provider_id, 'configured-oidc'),
            provider_subject = COALESCE(provider_subject, 'unavailable'),
            issued_at = COALESCE(issued_at, created_at)
        """
    )
    op.alter_column(TABLE, "provider_id", nullable=False, schema=SCHEMA)
    op.alter_column(TABLE, "provider_subject", nullable=False, schema=SCHEMA)
    op.alter_column(TABLE, "issued_at", nullable=False, schema=SCHEMA)
    op.create_check_constraint(
        "ck_portal_token_envelopes_token_refresh_failures",
        TABLE,
        "refresh_failures >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_portal_token_envelopes_token_lifecycle_state",
        TABLE,
        "lifecycle_state IN ('ACTIVE', 'REFRESH_PENDING', 'REFRESHING', "
        "'REFRESH_REQUIRED', 'REFRESH_FAILED', 'EXPIRED', 'LOGGED_OUT', "
        "'REVOKED', 'DISPOSED')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_portal_token_envelopes_refresh_due",
        TABLE,
        ["next_refresh_attempt_at", "expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text(
            "lifecycle_state IN ('ACTIVE', 'REFRESH_FAILED', 'REFRESH_REQUIRED', 'REFRESHING')"
        ),
    )
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON portal_control.portal_provider_logout_receipts "
        "TO portal_runtime"
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
        "REVOKE SELECT, INSERT, DELETE ON portal_control.portal_provider_logout_receipts "
        "FROM portal_runtime"
    )
    op.drop_index(
        "ix_portal_token_envelopes_refresh_due",
        table_name=TABLE,
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_portal_token_envelopes_token_lifecycle_state",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_portal_token_envelopes_token_refresh_failures",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    for column_name in (
        "updated_at",
        "refreshed_at",
        "refresh_expires_at",
        "expires_at",
        "issued_at",
        "last_failure_code",
        "next_refresh_attempt_at",
        "refresh_lease_expires_at",
        "refresh_lease_owner",
        "refresh_failures",
        "previous_refresh_token_fingerprint",
        "refresh_token_fingerprint",
        "lifecycle_state",
        "provider_session",
        "provider_subject",
        "provider_id",
    ):
        op.drop_column(TABLE, column_name, schema=SCHEMA)
    op.drop_index(
        "ix_portal_provider_logout_receipts_expiry",
        table_name="portal_provider_logout_receipts",
        schema=SCHEMA,
    )
    op.drop_table("portal_provider_logout_receipts", schema=SCHEMA)
