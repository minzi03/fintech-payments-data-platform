"""Serialize session creation, rotation, and principal-wide revocation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum

revision: str = "006_session_revocation_fence"
down_revision: str | None = "005_token_disposal_privilege"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "portal_control"
VERSION = "006_session_revocation_fence"


def upgrade() -> None:
    op.add_column(
        "portal_principals",
        sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=True),
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
    op.drop_column("portal_principals", "sessions_valid_after", schema=SCHEMA)
