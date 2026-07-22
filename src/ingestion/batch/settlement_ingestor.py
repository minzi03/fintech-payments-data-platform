"""Orchestration for idempotent settlement validation and immutable raw ingestion."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .contracts import SettlementContract
from .discovery import DiscoveryError, calculate_sha256, parse_settlement_filename
from .manifest import ManifestStore
from .models import (
    DuplicateKind,
    FilenameMetadata,
    FileValidationResult,
    ManifestRecord,
    ManifestStatus,
    ProcessingResult,
)
from .storage import SettlementStorage
from .validation import validate_settlement_file


class SettlementIngestor:
    """Coordinate one file at a time without coupling validation to local storage."""

    def __init__(
        self,
        *,
        contract: SettlementContract,
        manifest: ManifestStore,
        storage: SettlementStorage,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.contract = contract
        self.manifest = manifest
        self.storage = storage
        self.clock = clock or (lambda: datetime.now(UTC))
        self.run_id_factory = run_id_factory or (lambda: str(uuid4()))

    def ingest_many(
        self,
        paths: Iterable[Path],
        *,
        expected_partner_id: str,
        dry_run: bool = False,
        fail_on_rejected_records: bool = False,
    ) -> tuple[ProcessingResult, ...]:
        """Process files independently so one failure cannot prevent later manifests."""
        results = []
        for path in paths:
            try:
                result = self.ingest_file(
                    path,
                    expected_partner_id=expected_partner_id,
                    dry_run=dry_run,
                    fail_on_rejected_records=fail_on_rejected_records,
                )
            except Exception as error:  # keep discovery of later files independent
                result = ProcessingResult(
                    file_name=path.name,
                    status=ManifestStatus.FAILED,
                    checksum_sha256="",
                    duplicate_kind=DuplicateKind.NEW_FILE,
                    ingestion_run_id=self.run_id_factory(),
                    error_code="UNHANDLED_FILE_ERROR",
                    error_message=str(error),
                    dry_run=dry_run,
                )
            results.append(result)
        return tuple(results)

    def ingest_file(
        self,
        path: Path,
        *,
        expected_partner_id: str,
        dry_run: bool = False,
        fail_on_rejected_records: bool = False,
    ) -> ProcessingResult:
        """Validate and persist one file with retry-safe manifest ordering."""
        now = self.clock().astimezone(UTC)
        ingestion_run_id = self.run_id_factory()
        try:
            filename = parse_settlement_filename(
                path, self.contract.naming_pattern, expected_partner_id
            )
        except DiscoveryError as error:
            return self._handle_filename_rejection(
                path=path,
                partner_id=expected_partner_id,
                error=error,
                now=now,
                ingestion_run_id=ingestion_run_id,
                dry_run=dry_run,
            )

        checksum = calculate_sha256(path)
        if dry_run:
            validation = self._validate(path, filename, now, ingestion_run_id)
            return self._dry_run_result(
                path, filename, checksum, ingestion_run_id, validation, fail_on_rejected_records
            )

        record, duplicate_kind = self.manifest.register_discovery(
            partner_id=filename.partner_id,
            file_name=path.name,
            file_path=path,
            file_size_bytes=path.stat().st_size,
            checksum_sha256=checksum,
            schema_version=self.contract.schema_version,
            settlement_date=filename.settlement_date.isoformat(),
            discovered_at=now,
            ingestion_run_id=ingestion_run_id,
        )
        if record.status in {ManifestStatus.PROCESSED, ManifestStatus.QUARANTINED}:
            return self._result_from_manifest(record, duplicate_kind, path.name, skipped=True)

        record = self.manifest.transition(
            record.file_id,
            ManifestStatus.VALIDATING,
            file_path=str(path),
            file_size_bytes=path.stat().st_size,
            ingestion_run_id=ingestion_run_id,
            error_code=None,
            error_message=None,
        )
        validation = self._validate(path, filename, now, ingestion_run_id)
        if not validation.file_is_valid:
            return self._quarantine_invalid_file(
                path, filename, checksum, duplicate_kind, record, validation, now
            )

        record = self.manifest.transition(
            record.file_id,
            ManifestStatus.VALIDATED,
            record_count=validation.record_count,
            accepted_count=len(validation.accepted_records),
            rejected_count=len(validation.rejected_records),
        )
        if validation.rejected_records and fail_on_rejected_records:
            return self._quarantine_strict_file(
                path, filename, checksum, duplicate_kind, record, validation, now
            )

        record = self.manifest.transition(
            record.file_id,
            ManifestStatus.PROCESSING,
            processing_started_at=now.isoformat(),
        )
        try:
            metadata = {
                "checksum_sha256": checksum,
                "source_path": str(path),
                "ingested_at": now.isoformat(),
                "schema_version": self.contract.schema_version,
                "record_count": validation.record_count,
                "accepted_count": len(validation.accepted_records),
                "rejected_count": len(validation.rejected_records),
                "ingestion_run_id": ingestion_run_id,
            }
            bronze_path = self.storage.copy_to_bronze(
                path,
                partner_id=filename.partner_id,
                settlement_date=filename.settlement_date,
                ingestion_date=now.date(),
                checksum_sha256=checksum,
                metadata=metadata,
            )
            quarantine_path = None
            if validation.rejected_records:
                quarantine_path = self.storage.write_rejected_records(
                    validation.rejected_records,
                    source_file_name=path.name,
                    partner_id=filename.partner_id,
                    settlement_date=filename.settlement_date,
                    ingestion_date=now.date(),
                    checksum_sha256=checksum,
                )
            record = self.manifest.transition(
                record.file_id,
                ManifestStatus.PROCESSED,
                processed_at=now.isoformat(),
                bronze_path=bronze_path,
                quarantine_path=quarantine_path,
            )
        except Exception as error:
            return self._fail_manifest(record, duplicate_kind, path.name, error, now)
        return self._result_from_manifest(record, duplicate_kind, path.name)

    def _handle_filename_rejection(
        self,
        *,
        path: Path,
        partner_id: str,
        error: DiscoveryError,
        now: datetime,
        ingestion_run_id: str,
        dry_run: bool,
    ) -> ProcessingResult:
        try:
            checksum = calculate_sha256(path)
        except OSError as checksum_error:
            return ProcessingResult(
                file_name=path.name,
                status=ManifestStatus.FAILED,
                checksum_sha256="",
                duplicate_kind=DuplicateKind.NEW_FILE,
                ingestion_run_id=ingestion_run_id,
                error_code="CHECKSUM_FAILED",
                error_message=str(checksum_error),
                dry_run=dry_run,
            )
        if dry_run:
            return ProcessingResult(
                file_name=path.name,
                status=ManifestStatus.QUARANTINED,
                checksum_sha256=checksum,
                duplicate_kind=DuplicateKind.NEW_FILE,
                ingestion_run_id=ingestion_run_id,
                error_code=error.code,
                error_message=str(error),
                dry_run=True,
            )
        record, duplicate_kind = self.manifest.register_discovery(
            partner_id=partner_id,
            file_name=path.name,
            file_path=path,
            file_size_bytes=path.stat().st_size,
            checksum_sha256=checksum,
            schema_version=self.contract.schema_version,
            settlement_date=None,
            discovered_at=now,
            ingestion_run_id=ingestion_run_id,
        )
        if record.status is ManifestStatus.QUARANTINED:
            return self._result_from_manifest(record, duplicate_kind, path.name, skipped=True)
        record = self.manifest.transition(
            record.file_id,
            ManifestStatus.VALIDATING,
            ingestion_run_id=ingestion_run_id,
        )
        try:
            quarantine_path = self.storage.quarantine_file(
                path,
                partner_id=partner_id,
                settlement_date=None,
                ingestion_date=now.date(),
                checksum_sha256=checksum,
            )
            record = self.manifest.transition(
                record.file_id,
                ManifestStatus.QUARANTINED,
                processed_at=now.isoformat(),
                error_code=error.code,
                error_message=str(error),
                quarantine_path=quarantine_path,
            )
        except Exception as storage_error:
            return self._fail_manifest(record, duplicate_kind, path.name, storage_error, now)
        return self._result_from_manifest(record, duplicate_kind, path.name)

    def _quarantine_invalid_file(
        self,
        path: Path,
        filename: FilenameMetadata,
        checksum: str,
        duplicate_kind: DuplicateKind,
        record: ManifestRecord,
        validation: FileValidationResult,
        now: datetime,
    ) -> ProcessingResult:
        issue = validation.file_issues[0]
        try:
            quarantine_path = self.storage.quarantine_file(
                path,
                partner_id=filename.partner_id,
                settlement_date=filename.settlement_date,
                ingestion_date=now.date(),
                checksum_sha256=checksum,
            )
            record = self.manifest.transition(
                record.file_id,
                ManifestStatus.QUARANTINED,
                processed_at=now.isoformat(),
                record_count=validation.record_count,
                error_code=issue.code,
                error_message=issue.message,
                quarantine_path=quarantine_path,
            )
        except Exception as error:
            return self._fail_manifest(record, duplicate_kind, path.name, error, now)
        return self._result_from_manifest(record, duplicate_kind, path.name)

    def _quarantine_strict_file(
        self,
        path: Path,
        filename: FilenameMetadata,
        checksum: str,
        duplicate_kind: DuplicateKind,
        record: ManifestRecord,
        validation: FileValidationResult,
        now: datetime,
    ) -> ProcessingResult:
        try:
            quarantine_path = self.storage.quarantine_file(
                path,
                partner_id=filename.partner_id,
                settlement_date=filename.settlement_date,
                ingestion_date=now.date(),
                checksum_sha256=checksum,
            )
            self.storage.write_rejected_records(
                validation.rejected_records,
                source_file_name=path.name,
                partner_id=filename.partner_id,
                settlement_date=filename.settlement_date,
                ingestion_date=now.date(),
                checksum_sha256=checksum,
            )
            record = self.manifest.transition(
                record.file_id,
                ManifestStatus.QUARANTINED,
                processed_at=now.isoformat(),
                error_code="REJECTED_RECORDS_PRESENT",
                error_message="Strict mode quarantined a file containing rejected records",
                quarantine_path=quarantine_path,
            )
        except Exception as error:
            return self._fail_manifest(record, duplicate_kind, path.name, error, now)
        return self._result_from_manifest(record, duplicate_kind, path.name)

    def _fail_manifest(
        self,
        record: ManifestRecord,
        duplicate_kind: DuplicateKind,
        file_name: str,
        error: Exception,
        now: datetime,
    ) -> ProcessingResult:
        failed = self.manifest.transition(
            record.file_id,
            ManifestStatus.FAILED,
            processed_at=now.isoformat(),
            error_code="STORAGE_OR_PROCESSING_FAILED",
            error_message=str(error)[:1000],
        )
        return self._result_from_manifest(failed, duplicate_kind, file_name)

    def _validate(
        self,
        path: Path,
        filename: FilenameMetadata,
        now: datetime,
        ingestion_run_id: str,
    ) -> FileValidationResult:
        return validate_settlement_file(
            path,
            self.contract,
            filename,
            rejected_at=now,
            ingestion_run_id=ingestion_run_id,
        )

    @staticmethod
    def _dry_run_result(
        path: Path,
        filename: FilenameMetadata,
        checksum: str,
        ingestion_run_id: str,
        validation: FileValidationResult,
        fail_on_rejected_records: bool,
    ) -> ProcessingResult:
        quarantined = not validation.file_is_valid or (
            fail_on_rejected_records and bool(validation.rejected_records)
        )
        issue = validation.file_issues[0] if validation.file_issues else None
        return ProcessingResult(
            file_name=path.name,
            status=ManifestStatus.QUARANTINED if quarantined else ManifestStatus.VALIDATED,
            checksum_sha256=checksum,
            duplicate_kind=DuplicateKind.NEW_FILE,
            ingestion_run_id=ingestion_run_id,
            record_count=validation.record_count,
            accepted_count=len(validation.accepted_records),
            rejected_count=len(validation.rejected_records),
            error_code=issue.code if issue else None,
            error_message=issue.message if issue else None,
            dry_run=True,
        )

    @staticmethod
    def _result_from_manifest(
        record: ManifestRecord,
        duplicate_kind: DuplicateKind,
        file_name: str,
        *,
        skipped: bool = False,
    ) -> ProcessingResult:
        return ProcessingResult(
            file_id=record.file_id,
            file_name=file_name,
            status=record.status,
            checksum_sha256=record.checksum_sha256,
            duplicate_kind=duplicate_kind,
            ingestion_run_id=record.ingestion_run_id,
            record_count=record.record_count,
            accepted_count=record.accepted_count,
            rejected_count=record.rejected_count,
            bronze_path=Path(record.bronze_path) if record.bronze_path else None,
            quarantine_path=(Path(record.quarantine_path) if record.quarantine_path else None),
            error_code=record.error_code,
            error_message=record.error_message,
            skipped=skipped,
        )
