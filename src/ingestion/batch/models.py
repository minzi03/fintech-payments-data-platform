"""Typed models for settlement batch discovery, validation, and control state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


class ManifestStatus(StrEnum):
    """Durable lifecycle states for one unique settlement file content."""

    DISCOVERED = "DISCOVERED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class DuplicateKind(StrEnum):
    """Relationship between a discovery and prior manifest content."""

    NEW_FILE = "NEW_FILE"
    SAME_NAME_CHANGED_CONTENT = "SAME_NAME_CHANGED_CONTENT"
    SAME_NAME_SAME_CONTENT = "SAME_NAME_SAME_CONTENT"
    DIFFERENT_NAME_SAME_CONTENT = "DIFFERENT_NAME_SAME_CONTENT"


@dataclass(frozen=True, slots=True)
class FilenameMetadata:
    """Validated business metadata encoded in a settlement file name."""

    partner_id: str
    settlement_date: date
    sequence: int
    file_name: str


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    """One contract-valid settlement row normalized in memory."""

    partner_id: str
    settlement_date: date
    settlement_reference: str
    partner_transaction_reference: str
    internal_transaction_id: UUID | None
    transaction_timestamp: datetime
    amount: Decimal
    currency: str
    settlement_status: str
    fee_amount: Decimal
    net_amount: Decimal
    source_row_number: int


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One machine-readable file or record validation failure."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """One invalid source row retained with actionable context."""

    source_file: str
    source_row_number: int
    raw_record: dict[str, str | None]
    error_code: str
    error_message: str
    rejected_at: datetime
    ingestion_run_id: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        result = asdict(self)
        result["rejected_at"] = self.rejected_at.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class FileValidationResult:
    """File-level and row-level validation result before persistence."""

    file_issues: tuple[ValidationIssue, ...]
    accepted_records: tuple[SettlementRecord, ...]
    rejected_records: tuple[RejectedRecord, ...]
    record_count: int

    @property
    def file_is_valid(self) -> bool:
        return not self.file_issues


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    """One durable SQLite settlement manifest row."""

    file_id: str
    source_name: str
    partner_id: str
    file_name: str
    file_path: str
    file_size_bytes: int
    checksum_sha256: str
    schema_version: str
    settlement_date: str | None
    discovered_at: str
    processing_started_at: str | None
    processed_at: str | None
    status: ManifestStatus
    record_count: int
    accepted_count: int
    rejected_count: int
    error_code: str | None
    error_message: str | None
    bronze_path: str | None
    quarantine_path: str | None
    ingestion_run_id: str


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    """Structured outcome for one settlement file ingestion attempt."""

    file_name: str
    status: ManifestStatus
    checksum_sha256: str
    duplicate_kind: DuplicateKind
    ingestion_run_id: str
    file_id: str | None = None
    record_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    bronze_path: str | None = None
    quarantine_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    skipped: bool = False
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable CLI payload."""
        result = asdict(self)
        result["status"] = self.status.value
        result["duplicate_kind"] = self.duplicate_kind.value
        return result
