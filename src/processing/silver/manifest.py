"""Transactional SQLite manifest for incremental Bronze-to-Silver processing."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from processing.silver.models import (
    OutputType,
    ProcessingRun,
    ProcessingStatus,
    SilverOutput,
    SourceType,
    utc_iso,
)

TRANSITIONS = {
    ProcessingStatus.DISCOVERED: {
        ProcessingStatus.READING,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    },
    ProcessingStatus.READING: {
        ProcessingStatus.VALIDATING,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    },
    ProcessingStatus.VALIDATING: {
        ProcessingStatus.TRANSFORMING,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    },
    ProcessingStatus.TRANSFORMING: {
        ProcessingStatus.WRITING,
        ProcessingStatus.FAILED,
        ProcessingStatus.QUARANTINED,
    },
    ProcessingStatus.WRITING: {ProcessingStatus.COMPLETED, ProcessingStatus.FAILED},
    ProcessingStatus.COMPLETED: set(),
    ProcessingStatus.FAILED: set(),
    ProcessingStatus.QUARANTINED: set(),
}


class ProcessingManifestError(RuntimeError):
    pass


class ProcessingManifest(Protocol):
    def find_latest_identity(
        self,
        *,
        pipeline_name: str,
        input_checksum: str,
        code_version: str,
        schema_version: str,
    ) -> ProcessingRun | None: ...

    def find_completed(
        self,
        *,
        pipeline_name: str,
        input_checksum: str,
        code_version: str,
        schema_version: str,
    ) -> ProcessingRun | None: ...

    def latest_output(
        self,
        entity_name: str,
        output_type: OutputType,
        *,
        exclude_input_checksum: str | None = None,
    ) -> SilverOutput | None: ...


class SqliteProcessingManifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous = FULL")
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
                CREATE TABLE IF NOT EXISTS silver_processing_manifest (
                    run_id TEXT PRIMARY KEY,
                    pipeline_name TEXT NOT NULL,
                    source_type TEXT NOT NULL CHECK (source_type IN ('CDC', 'SETTLEMENT')),
                    entity_name TEXT NOT NULL,
                    input_object_uri TEXT NOT NULL,
                    input_checksum TEXT NOT NULL,
                    input_record_count INTEGER NOT NULL DEFAULT 0 CHECK (input_record_count >= 0),
                    status TEXT NOT NULL CHECK (status IN (
                        'DISCOVERED', 'READING', 'VALIDATING', 'TRANSFORMING',
                        'WRITING', 'COMPLETED', 'FAILED', 'QUARANTINED'
                    )),
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    output_object_uris TEXT NOT NULL DEFAULT '[]',
                    output_descriptors_json TEXT NOT NULL DEFAULT '[]',
                    output_record_count INTEGER NOT NULL DEFAULT 0 CHECK (output_record_count >= 0),
                    rejected_record_count INTEGER NOT NULL DEFAULT 0
                        CHECK (rejected_record_count >= 0),
                    error_code TEXT,
                    error_message TEXT,
                    code_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_silver_manifest_identity
                ON silver_processing_manifest (
                    pipeline_name, input_checksum, code_version, schema_version, status
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_silver_manifest_latest
                ON silver_processing_manifest (entity_name, status, completed_at)
                """
            )

    def register(
        self,
        *,
        run_id: str,
        pipeline_name: str,
        source_type: SourceType,
        entity_name: str,
        input_object_uri: str,
        input_checksum: str,
        code_version: str,
        schema_version: str,
        started_at: datetime,
    ) -> ProcessingRun:
        with self._session() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO silver_processing_manifest (
                        run_id, pipeline_name, source_type, entity_name, input_object_uri,
                        input_checksum, status, started_at, code_version, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        pipeline_name,
                        source_type.value,
                        entity_name,
                        input_object_uri,
                        input_checksum,
                        ProcessingStatus.DISCOVERED.value,
                        utc_iso(started_at),
                        code_version,
                        schema_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ProcessingManifestError(f"Processing run already exists: {run_id}") from error
        record = self.get(run_id)
        assert record is not None
        return record

    def transition(
        self,
        run_id: str,
        status: ProcessingStatus,
        **updates: object,
    ) -> ProcessingRun:
        allowed_columns = {
            "input_record_count",
            "completed_at",
            "output_object_uris",
            "output_descriptors_json",
            "output_record_count",
            "rejected_record_count",
            "error_code",
            "error_message",
        }
        if unsupported := set(updates) - allowed_columns:
            raise ProcessingManifestError(f"Unsupported manifest updates: {sorted(unsupported)}")
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM silver_processing_manifest WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ProcessingManifestError(f"Unknown processing run: {run_id}")
            current = ProcessingStatus(row["status"])
            if status not in TRANSITIONS[current]:
                raise ProcessingManifestError(
                    f"Invalid processing transition {current.value} -> {status.value}"
                )
            assignments = ["status = ?"]
            parameters: list[object] = [status.value]
            for column, value in updates.items():
                assignments.append(f"{column} = ?")
                parameters.append(value)
            parameters.extend((run_id, current.value))
            cursor = connection.execute(
                f"UPDATE silver_processing_manifest SET {', '.join(assignments)} "
                "WHERE run_id = ? AND status = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise ProcessingManifestError("Concurrent processing manifest update detected")
        updated = self.get(run_id)
        assert updated is not None
        return updated

    def mark_reading(self, run_id: str) -> ProcessingRun:
        return self.transition(run_id, ProcessingStatus.READING)

    def mark_validating(self, run_id: str, input_count: int) -> ProcessingRun:
        return self.transition(run_id, ProcessingStatus.VALIDATING, input_record_count=input_count)

    def mark_transforming(self, run_id: str) -> ProcessingRun:
        return self.transition(run_id, ProcessingStatus.TRANSFORMING)

    def mark_writing(self, run_id: str) -> ProcessingRun:
        return self.transition(run_id, ProcessingStatus.WRITING)

    def mark_completed(
        self,
        run_id: str,
        *,
        outputs: tuple[SilverOutput, ...],
        output_record_count: int,
        rejected_record_count: int,
        completed_at: datetime,
    ) -> ProcessingRun:
        return self.transition(
            run_id,
            ProcessingStatus.COMPLETED,
            completed_at=utc_iso(completed_at),
            output_object_uris=json.dumps([item.object_uri for item in outputs]),
            output_descriptors_json=json.dumps(
                [item.to_dict() for item in outputs], sort_keys=True
            ),
            output_record_count=output_record_count,
            rejected_record_count=rejected_record_count,
            error_code=None,
            error_message=None,
        )

    def mark_failed(self, run_id: str, error: Exception) -> ProcessingRun:
        current = self.get(run_id)
        if current is None:
            raise ProcessingManifestError(f"Unknown processing run: {run_id}")
        if current.status in {ProcessingStatus.COMPLETED, ProcessingStatus.QUARANTINED}:
            return current
        return self.transition(
            run_id,
            ProcessingStatus.FAILED,
            completed_at=utc_iso(datetime.now(UTC)),
            error_code=type(error).__name__,
            error_message=str(error)[:1000],
        )

    def mark_quarantined(
        self,
        run_id: str,
        *,
        error_code: str,
        error_message: str,
        rejected_record_count: int,
        completed_at: datetime,
    ) -> ProcessingRun:
        return self.transition(
            run_id,
            ProcessingStatus.QUARANTINED,
            completed_at=utc_iso(completed_at),
            rejected_record_count=rejected_record_count,
            error_code=error_code,
            error_message=error_message[:1000],
        )

    def get(self, run_id: str) -> ProcessingRun | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM silver_processing_manifest WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _record(row) if row is not None else None

    def list_all(self) -> tuple[ProcessingRun, ...]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM silver_processing_manifest ORDER BY started_at, run_id"
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def find_completed(
        self,
        *,
        pipeline_name: str,
        input_checksum: str,
        code_version: str,
        schema_version: str,
    ) -> ProcessingRun | None:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM silver_processing_manifest
                WHERE pipeline_name = ? AND input_checksum = ? AND code_version = ?
                  AND schema_version = ? AND status = 'COMPLETED'
                ORDER BY completed_at DESC, run_id DESC LIMIT 1
                """,
                (pipeline_name, input_checksum, code_version, schema_version),
            ).fetchone()
        return _record(row) if row is not None else None

    def find_latest_identity(
        self,
        *,
        pipeline_name: str,
        input_checksum: str,
        code_version: str,
        schema_version: str,
    ) -> ProcessingRun | None:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM silver_processing_manifest
                WHERE pipeline_name = ? AND input_checksum = ? AND code_version = ?
                  AND schema_version = ?
                ORDER BY started_at DESC, run_id DESC LIMIT 1
                """,
                (pipeline_name, input_checksum, code_version, schema_version),
            ).fetchone()
        return _record(row) if row is not None else None

    def latest_output(
        self,
        entity_name: str,
        output_type: OutputType,
        *,
        exclude_input_checksum: str | None = None,
    ) -> SilverOutput | None:
        exclusion = " AND input_checksum != ?" if exclude_input_checksum is not None else ""
        parameters: tuple[object, ...] = (
            (entity_name, exclude_input_checksum)
            if exclude_input_checksum is not None
            else (entity_name,)
        )
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT output_descriptors_json FROM silver_processing_manifest
                WHERE entity_name = ? AND status = 'COMPLETED'
                {exclusion}
                ORDER BY completed_at DESC, run_id DESC
                """,
                parameters,
            ).fetchall()
        for row in rows:
            for raw in json.loads(row["output_descriptors_json"]):
                if raw["output_type"] == output_type.value:
                    return SilverOutput(
                        output_type=output_type,
                        object_uri=raw["object_uri"],
                        checksum_sha256=raw["checksum_sha256"],
                        record_count=int(raw["record_count"]),
                    )
        return None


def _record(row: sqlite3.Row) -> ProcessingRun:
    outputs = tuple(
        SilverOutput(
            output_type=OutputType(item["output_type"]),
            object_uri=item["object_uri"],
            checksum_sha256=item["checksum_sha256"],
            record_count=int(item["record_count"]),
        )
        for item in json.loads(row["output_descriptors_json"])
    )
    return ProcessingRun(
        run_id=row["run_id"],
        pipeline_name=row["pipeline_name"],
        source_type=SourceType(row["source_type"]),
        entity_name=row["entity_name"],
        input_object_uri=row["input_object_uri"],
        input_checksum=row["input_checksum"],
        input_record_count=int(row["input_record_count"]),
        status=ProcessingStatus(row["status"]),
        started_at=datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")),
        completed_at=(
            datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
            if row["completed_at"]
            else None
        ),
        output_object_uris=tuple(json.loads(row["output_object_uris"])),
        outputs=outputs,
        output_record_count=int(row["output_record_count"]),
        rejected_record_count=int(row["rejected_record_count"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        code_version=row["code_version"],
        schema_version=row["schema_version"],
    )
