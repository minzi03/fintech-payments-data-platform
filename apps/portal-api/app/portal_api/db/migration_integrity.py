"""Authoritative migration checksum validation for the Alembic runner."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

MIGRATION_LOCK_KEY = 7_162_002_008


class MigrationIntegrityError(RuntimeError):
    """Raised when recorded migration history differs from the repository chain."""


def migration_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_recorded_migration_checksums(
    connection: Connection,
    versions_directory: Path,
) -> None:
    """Reject edited or missing revisions that have already been applied."""
    history_exists = connection.execute(
        text("SELECT to_regclass('portal_control.schema_migrations')")
    ).scalar_one()
    if history_exists is None:
        return

    repository_versions = {
        path.stem: migration_checksum(path)
        for path in versions_directory.glob("[0-9][0-9][0-9]_*.py")
        if path.is_file()
    }
    recorded = connection.execute(
        text("SELECT version, checksum FROM portal_control.schema_migrations")
    ).all()
    for version, checksum in recorded:
        expected = repository_versions.get(str(version))
        if expected is None:
            raise MigrationIntegrityError(
                "An applied Portal migration is absent from the repository chain"
            )
        if expected != str(checksum):
            raise MigrationIntegrityError("An applied Portal migration checksum does not match")
