"""Transactional SQLite control store for CDC Bronze micro-batches."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ingestion.cdc_consumer.models import (
    BatchManifestRecord,
    BatchStatus,
    CdcBatch,
    utc_isoformat,
)

_ALLOWED_TRANSITIONS = {
    BatchStatus.COLLECTING: {BatchStatus.SERIALIZING, BatchStatus.FAILED},
    BatchStatus.SERIALIZING: {BatchStatus.UPLOADING, BatchStatus.FAILED},
    BatchStatus.UPLOADING: {BatchStatus.UPLOADED, BatchStatus.FAILED},
    BatchStatus.UPLOADED: {BatchStatus.COMMITTED},
    BatchStatus.FAILED: {BatchStatus.SERIALIZING},
    BatchStatus.COMMITTED: set(),
}


class ManifestError(RuntimeError):
    """Base error for durable CDC control state."""


class ManifestConflictError(ManifestError):
    """Raised when one deterministic batch identity maps to different coordinates."""


class InvalidManifestTransition(ManifestError):
    """Raised when code attempts to skip the batch lifecycle."""


class BatchManifest(Protocol):
    def register(self, batch: CdcBatch, *, consumer_group: str) -> BatchManifestRecord: ...

    def get(self, batch_id: str) -> BatchManifestRecord | None: ...

    def mark_serializing(self, batch_id: str) -> BatchManifestRecord: ...

    def mark_uploading(self, batch_id: str) -> BatchManifestRecord: ...

    def mark_uploaded(
        self, batch_id: str, *, checksum_sha256: str, object_uri: str
    ) -> BatchManifestRecord: ...

    def mark_committed(self, batch_id: str) -> BatchManifestRecord: ...

    def mark_failed(
        self, batch_id: str, *, error_code: str, error_message: str
    ) -> BatchManifestRecord: ...

    def note_retryable_error(
        self, batch_id: str, *, error_code: str, error_message: str
    ) -> BatchManifestRecord: ...

    def recoverable(self, *, consumer_group: str) -> list[BatchManifestRecord]: ...


class SqliteBatchManifest:
    """Small local manifest with full-sync transactions and a migration boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cdc_batch_manifest (
                    batch_id TEXT PRIMARY KEY,
                    consumer_group TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    partition INTEGER NOT NULL CHECK (partition >= 0),
                    offset_start INTEGER NOT NULL CHECK (offset_start >= 0),
                    offset_end INTEGER NOT NULL CHECK (offset_end >= offset_start),
                    status TEXT NOT NULL CHECK (status IN (
                        'COLLECTING', 'SERIALIZING', 'UPLOADING',
                        'UPLOADED', 'COMMITTED', 'FAILED'
                    )),
                    record_count INTEGER NOT NULL CHECK (record_count > 0),
                    schema_version TEXT NOT NULL,
                    checksum_sha256 TEXT,
                    object_uri TEXT,
                    created_at TEXT NOT NULL,
                    upload_started_at TEXT,
                    uploaded_at TEXT,
                    committed_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                    UNIQUE (consumer_group, topic, partition, offset_start, offset_end)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cdc_manifest_recovery
                ON cdc_batch_manifest (consumer_group, status, topic, partition, offset_end)
                """
            )

    def register(self, batch: CdcBatch, *, consumer_group: str) -> BatchManifestRecord:
        now = utc_isoformat(datetime.now(UTC))
        with self._session() as connection:
            existing = connection.execute(
                "SELECT * FROM cdc_batch_manifest WHERE batch_id = ?", (batch.batch_id,)
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO cdc_batch_manifest (
                            batch_id, consumer_group, topic, partition,
                            offset_start, offset_end, status, record_count,
                            schema_version, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            batch.batch_id,
                            consumer_group,
                            batch.topic,
                            batch.partition,
                            batch.offset_start,
                            batch.offset_end,
                            BatchStatus.COLLECTING.value,
                            batch.record_count,
                            batch.schema_version,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ManifestConflictError(
                        "Offset range is already registered under a different batch identity"
                    ) from exc
                existing = connection.execute(
                    "SELECT * FROM cdc_batch_manifest WHERE batch_id = ?", (batch.batch_id,)
                ).fetchone()
            assert existing is not None
            record = _record(existing)
            expected = (
                consumer_group,
                batch.topic,
                batch.partition,
                batch.offset_start,
                batch.offset_end,
                batch.record_count,
                batch.schema_version,
            )
            actual = (
                record.consumer_group,
                record.topic,
                record.partition,
                record.offset_start,
                record.offset_end,
                record.record_count,
                record.schema_version,
            )
            if actual != expected:
                raise ManifestConflictError(
                    "Deterministic batch ID already exists with different coordinates"
                )
            return record

    def get(self, batch_id: str) -> BatchManifestRecord | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM cdc_batch_manifest WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return _record(row) if row is not None else None

    def mark_serializing(self, batch_id: str) -> BatchManifestRecord:
        current = self._require(batch_id)
        if current.status in {
            BatchStatus.SERIALIZING,
            BatchStatus.UPLOADING,
            BatchStatus.UPLOADED,
            BatchStatus.COMMITTED,
        }:
            return current
        return self._transition(batch_id, BatchStatus.SERIALIZING)

    def mark_uploading(self, batch_id: str) -> BatchManifestRecord:
        current = self._require(batch_id)
        if current.status in {BatchStatus.UPLOADING, BatchStatus.UPLOADED, BatchStatus.COMMITTED}:
            return current
        return self._transition(
            batch_id,
            BatchStatus.UPLOADING,
            upload_started_at=utc_isoformat(datetime.now(UTC)),
        )

    def mark_uploaded(
        self, batch_id: str, *, checksum_sha256: str, object_uri: str
    ) -> BatchManifestRecord:
        if len(checksum_sha256) != 64:
            raise ManifestError("Uploaded checksum must be a SHA-256 hex digest")
        current = self._require(batch_id)
        if current.status in {BatchStatus.UPLOADED, BatchStatus.COMMITTED}:
            if current.checksum_sha256 != checksum_sha256 or current.object_uri != object_uri:
                raise ManifestConflictError("Uploaded artifact differs from manifest evidence")
            return current
        return self._transition(
            batch_id,
            BatchStatus.UPLOADED,
            checksum_sha256=checksum_sha256,
            object_uri=object_uri,
            uploaded_at=utc_isoformat(datetime.now(UTC)),
            error_code=None,
            error_message=None,
        )

    def mark_committed(self, batch_id: str) -> BatchManifestRecord:
        current = self._require(batch_id)
        if current.status is BatchStatus.COMMITTED:
            return current
        return self._transition(
            batch_id,
            BatchStatus.COMMITTED,
            committed_at=utc_isoformat(datetime.now(UTC)),
            error_code=None,
            error_message=None,
        )

    def mark_failed(
        self, batch_id: str, *, error_code: str, error_message: str
    ) -> BatchManifestRecord:
        current = self._require(batch_id)
        if current.status in {BatchStatus.UPLOADED, BatchStatus.COMMITTED}:
            raise InvalidManifestTransition("Uploaded evidence cannot be downgraded to FAILED")
        if current.status is BatchStatus.FAILED:
            return self.note_retryable_error(
                batch_id, error_code=error_code, error_message=error_message
            )
        return self._transition(
            batch_id,
            BatchStatus.FAILED,
            error_code=error_code,
            error_message=_safe_error(error_message),
            retry_count=current.retry_count + 1,
        )

    def note_retryable_error(
        self, batch_id: str, *, error_code: str, error_message: str
    ) -> BatchManifestRecord:
        current = self._require(batch_id)
        with self._session() as connection:
            connection.execute(
                """
                UPDATE cdc_batch_manifest
                SET error_code = ?, error_message = ?, retry_count = ?
                WHERE batch_id = ? AND status = ?
                """,
                (
                    error_code,
                    _safe_error(error_message),
                    current.retry_count + 1,
                    batch_id,
                    current.status.value,
                ),
            )
        return self._require(batch_id)

    def recoverable(self, *, consumer_group: str) -> list[BatchManifestRecord]:
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM cdc_batch_manifest
                WHERE consumer_group = ? AND status IN ('UPLOADED', 'COMMITTED')
                ORDER BY topic, partition, offset_end
                """,
                (consumer_group,),
            ).fetchall()
        return [_record(row) for row in rows]

    def list_all(self) -> list[BatchManifestRecord]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM cdc_batch_manifest ORDER BY created_at, batch_id"
            ).fetchall()
        return [_record(row) for row in rows]

    def _require(self, batch_id: str) -> BatchManifestRecord:
        record = self.get(batch_id)
        if record is None:
            raise ManifestError(f"Unknown CDC batch: {batch_id}")
        return record

    def _transition(
        self,
        batch_id: str,
        target: BatchStatus,
        **updates: object,
    ) -> BatchManifestRecord:
        current = self._require(batch_id)
        if target not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidManifestTransition(
                f"Invalid manifest transition: {current.status.value} -> {target.value}"
            )
        assignments = ["status = ?"]
        values: list[object] = [target.value]
        for column, value in updates.items():
            if column not in {
                "checksum_sha256",
                "object_uri",
                "upload_started_at",
                "uploaded_at",
                "committed_at",
                "error_code",
                "error_message",
                "retry_count",
            }:
                raise ManifestError(f"Unsupported manifest field: {column}")
            assignments.append(f"{column} = ?")
            values.append(value)
        values.extend([batch_id, current.status.value])
        with self._session() as connection:
            cursor = connection.execute(
                f"UPDATE cdc_batch_manifest SET {', '.join(assignments)} "
                "WHERE batch_id = ? AND status = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise ManifestError("Manifest state changed concurrently")
        return self._require(batch_id)


def _safe_error(message: str) -> str:
    return message.replace("\r", " ").replace("\n", " ")[:1024]


def _parse_timestamp(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _record(row: sqlite3.Row) -> BatchManifestRecord:
    return BatchManifestRecord(
        batch_id=str(row["batch_id"]),
        consumer_group=str(row["consumer_group"]),
        topic=str(row["topic"]),
        partition=int(row["partition"]),
        offset_start=int(row["offset_start"]),
        offset_end=int(row["offset_end"]),
        status=BatchStatus(str(row["status"])),
        record_count=int(row["record_count"]),
        schema_version=str(row["schema_version"]),
        checksum_sha256=row["checksum_sha256"],
        object_uri=row["object_uri"],
        created_at=_parse_timestamp(str(row["created_at"])) or datetime.now(UTC),
        upload_started_at=_parse_timestamp(row["upload_started_at"]),
        uploaded_at=_parse_timestamp(row["uploaded_at"]),
        committed_at=_parse_timestamp(row["committed_at"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        retry_count=int(row["retry_count"]),
    )


def highest_ranges(
    records: Iterable[BatchManifestRecord],
) -> dict[tuple[str, int], BatchManifestRecord]:
    result: dict[tuple[str, int], BatchManifestRecord] = {}
    for record in records:
        key = (record.topic, record.partition)
        if key not in result or result[key].offset_end < record.offset_end:
            result[key] = record
    return result
