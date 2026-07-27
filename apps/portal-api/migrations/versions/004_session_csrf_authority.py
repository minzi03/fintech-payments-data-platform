"""Add synchronizer-token authority to server-side sessions."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum

revision: str = "004_session_csrf_authority"
down_revision: str | None = "003_callback_runtime_privileges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
VERSION = "004_session_csrf_authority"


def upgrade() -> None:
    op.add_column(
        "portal_sessions",
        sa.Column("csrf_token_hash", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "portal_sessions",
        sa.Column("csrf_generation", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE portal_control.portal_sessions
        SET status = CASE
              WHEN status IN ('ACTIVE', 'REFRESH_REQUIRED') THEN 'INVALID'
              ELSE status
            END,
            revoked_at = CASE
              WHEN status IN ('ACTIVE', 'REFRESH_REQUIRED') THEN CURRENT_TIMESTAMP
              ELSE revoked_at
            END,
            revoked_reason = CASE
              WHEN status IN ('ACTIVE', 'REFRESH_REQUIRED') THEN 'SCHEMA_SECURITY_ROTATION'
              ELSE revoked_reason
            END,
            csrf_token_hash = decode(repeat('00', 32), 'hex'),
            csrf_generation = 1
        """
    )
    op.alter_column(
        "portal_sessions",
        "csrf_token_hash",
        nullable=False,
        schema=SCHEMA,
    )
    op.alter_column(
        "portal_sessions",
        "csrf_generation",
        nullable=False,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_portal_sessions_session_csrf_generation",
        "portal_sessions",
        "csrf_generation > 0",
        schema=SCHEMA,
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
    op.drop_constraint(
        "ck_portal_sessions_session_csrf_generation",
        "portal_sessions",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("portal_sessions", "csrf_generation", schema=SCHEMA)
    op.drop_column("portal_sessions", "csrf_token_hash", schema=SCHEMA)
