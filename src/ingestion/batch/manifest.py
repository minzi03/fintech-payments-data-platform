"""SQLite control store for retry-safe settlement file manifest state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .discovery import deterministic_file_id
from .models import DuplicateKind, ManifestRecord, ManifestStatus

SOURCE_NAME = "banking_partner_settlement"

ALLOWED_TRANSITIONS: dict[ManifestStatus, frozenset[ManifestStatus]] = {
    ManifestStatus.DISCOVERED: frozenset({ManifestStatus.VALIDATING, ManifestStatus.FAILED}),
    ManifestStatus.VALIDATING: frozenset(
        {
            ManifestStatus.VALIDATING,
            ManifestStatus.VALIDATED,
            ManifestStatus.QUARANTINED,
            ManifestStatus.FAILED,
        }
    ),
    ManifestStatus.VALIDATED: frozenset(
        {
            ManifestStatus.VALIDATING,
            ManifestStatus.PROCESSING,
            ManifestStatus.QUARANTINED,
            ManifestStatus.FAILED,
        }
    ),
    ManifestStatus.PROCESSING: frozenset(
        {
            ManifestStatus.VALIDATING,
            ManifestStatus.PROCESSED,
            ManifestStatus.QUARANTINED,
            ManifestStatus.FAILED,
        }
    ),
    ManifestStatus.FAILED: frozenset({ManifestStatus.VALIDATING}),
    ManifestStatus.QUARANTINED: frozenset(),
    ManifestStatus.PROCESSED: frozenset(),
}

UPDATABLE_COLUMNS = {
    "file_path",
    "file_size_bytes",
    "processing_started_at",
    "processed_at",
    "record_count",
    "accepted_count",
    "rejected_count",
    "error_code",
    "error_message",
    "bronze_path",
    "quarantine_path",
    "ingestion_run_id",
}


class ManifestError(RuntimeError):
    """Raised for missing records or illegal manifest lifecycle transitions."""


class ManifestStore:
    """Persist one manifest row per unique source, partner, and content checksum."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        """Create the SQLite control database and indexes idempotently."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settlement_file_manifest (
                    file_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    partner_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),
                    checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
                    schema_version TEXT NOT NULL,
                    settlement_date TEXT,
                    discovered_at TEXT NOT NULL,
                    processing_started_at TEXT,
                    processed_at TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'DISCOVERED', 'VALIDATING', 'VALIDATED', 'PROCESSING',
                            'PROCESSED', 'QUARANTINED', 'FAILED'
                        )
                    ),
                    record_count INTEGER NOT NULL DEFAULT 0 CHECK (record_count >= 0),
                    accepted_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
                    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
                    error_code TEXT,
                    error_message TEXT,
                    bronze_path TEXT,
                    quarantine_path TEXT,
                    ingestion_run_id TEXT NOT NULL,
                    UNIQUE (source_name, partner_id, checksum_sha256)
                );

                CREATE INDEX IF NOT EXISTS ix_settlement_manifest_file_name
                    ON settlement_file_manifest (source_name, partner_id, file_name, discovered_at);

                CREATE INDEX IF NOT EXISTS ix_settlement_manifest_status
                    ON settlement_file_manifest (status, discovered_at);
                """
            )

    def register_discovery(
        self,
        *,
        partner_id: str,
        file_name: str,
        file_path: Path,
        file_size_bytes: int,
        checksum_sha256: str,
        schema_version: str,
        settlement_date: str | None,
        discovered_at: datetime,
        ingestion_run_id: str,
    ) -> tuple[ManifestRecord, DuplicateKind]:
        """Insert a discovery or classify it against an existing content identity."""
        self.initialize()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM settlement_file_manifest
                WHERE source_name = ? AND partner_id = ? AND checksum_sha256 = ?
                """,
                (SOURCE_NAME, partner_id, checksum_sha256),
            ).fetchone()
            if existing is not None:
                duplicate_kind = (
                    DuplicateKind.SAME_NAME_SAME_CONTENT
                    if existing["file_name"] == file_name
                    else DuplicateKind.DIFFERENT_NAME_SAME_CONTENT
                )
                return self._to_record(existing), duplicate_kind

            prior_name = connection.execute(
                """
                SELECT checksum_sha256 FROM settlement_file_manifest
                WHERE source_name = ? AND partner_id = ? AND file_name = ?
                ORDER BY discovered_at DESC LIMIT 1
                """,
                (SOURCE_NAME, partner_id, file_name),
            ).fetchone()
            duplicate_kind = (
                DuplicateKind.SAME_NAME_CHANGED_CONTENT
                if prior_name is not None
                else DuplicateKind.NEW_FILE
            )
            file_id = deterministic_file_id(SOURCE_NAME, partner_id, checksum_sha256)
            connection.execute(
                """
                INSERT INTO settlement_file_manifest (
                    file_id, source_name, partner_id, file_name, file_path, file_size_bytes,
                    checksum_sha256, schema_version, settlement_date, discovered_at,
                    status, ingestion_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    SOURCE_NAME,
                    partner_id,
                    file_name,
                    str(file_path),
                    file_size_bytes,
                    checksum_sha256,
                    schema_version,
                    settlement_date,
                    discovered_at.isoformat(),
                    ManifestStatus.DISCOVERED.value,
                    ingestion_run_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM settlement_file_manifest WHERE file_id = ?", (file_id,)
            ).fetchone()
            if row is None:  # pragma: no cover - defensive SQLite invariant
                raise ManifestError(f"Manifest insert did not return file_id {file_id}")
            return self._to_record(row), duplicate_kind

    def transition(
        self,
        file_id: str,
        new_status: ManifestStatus,
        **updates: Any,
    ) -> ManifestRecord:
        """Atomically validate and apply one status transition with controlled fields."""
        unknown_fields = set(updates) - UPDATABLE_COLUMNS
        if unknown_fields:
            raise ManifestError(f"Unsupported manifest update fields: {sorted(unknown_fields)}")
        self.initialize()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM settlement_file_manifest WHERE file_id = ?", (file_id,)
            ).fetchone()
            if current is None:
                raise ManifestError(f"Manifest file_id does not exist: {file_id}")
            current_status = ManifestStatus(current["status"])
            if new_status not in ALLOWED_TRANSITIONS[current_status]:
                raise ManifestError(
                    f"Invalid manifest transition {current_status.value} -> {new_status.value}"
                )

            assignments = ["status = ?"]
            parameters: list[Any] = [new_status.value]
            for column, value in updates.items():
                assignments.append(f"{column} = ?")
                parameters.append(str(value) if isinstance(value, Path) else value)
            parameters.extend((file_id, current_status.value))
            cursor = connection.execute(
                f"UPDATE settlement_file_manifest SET {', '.join(assignments)} "
                "WHERE file_id = ? AND status = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise ManifestError(f"Concurrent manifest update detected for {file_id}")
            updated = connection.execute(
                "SELECT * FROM settlement_file_manifest WHERE file_id = ?", (file_id,)
            ).fetchone()
            if updated is None:  # pragma: no cover - defensive SQLite invariant
                raise ManifestError(f"Manifest update lost file_id {file_id}")
            return self._to_record(updated)

    def get(self, file_id: str) -> ManifestRecord | None:
        """Read one manifest record by deterministic file ID."""
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM settlement_file_manifest WHERE file_id = ?", (file_id,)
            ).fetchone()
        return self._to_record(row) if row is not None else None

    def list_all(self) -> tuple[ManifestRecord, ...]:
        """Read manifest history in discovery order for tests and local operations."""
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM settlement_file_manifest ORDER BY discovered_at, file_id"
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> ManifestRecord:
        payload = dict(row)
        payload["status"] = ManifestStatus(payload["status"])
        return ManifestRecord(**payload)
