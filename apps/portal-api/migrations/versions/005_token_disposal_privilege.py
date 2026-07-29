"""Allow the session runtime to dispose encrypted provider token envelopes."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from portal_api.db.migration_integrity import migration_checksum

revision: str = "005_token_disposal_privilege"
down_revision: str | None = "004_session_csrf_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSION = "005_token_disposal_privilege"


def upgrade() -> None:
    op.execute("GRANT DELETE ON portal_control.portal_token_envelopes TO portal_runtime")
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
    op.execute("REVOKE DELETE ON portal_control.portal_token_envelopes FROM portal_runtime")
