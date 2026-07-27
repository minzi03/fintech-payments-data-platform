"""Add durable login intent storage for governed login initiation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum
from sqlalchemy.dialects import postgresql

revision: str = "002_login_intent_and_initiation"
down_revision: str | None = "001_initial_portal_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
VERSION = "002_login_intent_and_initiation"


def upgrade() -> None:
    op.create_table(
        "portal_login_intents",
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("intent_lookup_hash", sa.LargeBinary(), nullable=False),
        sa.Column("selected_provider", sa.String(128), nullable=False),
        sa.Column("validated_return_path", sa.Text(), nullable=True),
        sa.Column("browser_binding_reference", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "intent_lookup_hash",
            name="uq_portal_login_intents_intent_lookup_hash",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONSUMED', 'EXPIRED')",
            name="ck_portal_login_intents_login_intent_status",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_portal_login_intents_login_intent_version",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_portal_login_intents_pending",
        "portal_login_intents",
        ["expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON portal_control.portal_login_intents
          TO portal_runtime
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
    op.drop_index(
        "ix_portal_login_intents_pending",
        table_name="portal_login_intents",
        schema=SCHEMA,
    )
    op.drop_table("portal_login_intents", schema=SCHEMA)
