"""Settlement-specific object layout over shared immutable storage backends."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

from common.storage import ImmutableCollisionError, LocalStorageBackend, StorageBackend

from .models import RejectedRecord

SETTLEMENT_PREFIX = "settlements"
LOCAL_BRONZE_BUCKET = "local-bronze"
LOCAL_QUARANTINE_BUCKET = "local-quarantine"

# Phase 2 compatibility for callers that imported the settlement-specific name.
ImmutableStorageError = ImmutableCollisionError


class SettlementStorage(Protocol):
    """Persistence operations required by ingestion without SDK coupling."""

    def copy_to_bronze(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        ingestion_run_id: str,
        metadata: Mapping[str, object],
    ) -> str: ...

    def quarantine_file(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date | None,
        ingestion_run_id: str,
        checksum_sha256: str,
        metadata: Mapping[str, object],
    ) -> str: ...

    def write_rejected_records(
        self,
        records: Sequence[RejectedRecord],
        *,
        source_file_name: str,
        partner_id: str,
        settlement_date: date,
        ingestion_run_id: str,
        metadata: Mapping[str, object],
    ) -> str: ...


def build_bronze_object_key(
    *,
    partner_id: str,
    settlement_date: date,
    ingestion_date: date,
    checksum_sha256: str,
    file_name: str,
) -> str:
    """Build one deterministic content-addressed settlement Bronze key."""
    _validate_partition_value(partner_id, "partner_id")
    if re.fullmatch(r"[0-9a-f]{64}", checksum_sha256) is None:
        raise ValueError("checksum_sha256 must be 64 lowercase hexadecimal characters")
    safe_name = _safe_file_name(file_name)
    return (
        f"{SETTLEMENT_PREFIX}/partner_id={partner_id}/"
        f"settlement_date={settlement_date.isoformat()}/"
        f"ingestion_date={ingestion_date.isoformat()}/"
        f"checksum={checksum_sha256}/{safe_name}"
    )


def build_quarantine_object_key(
    *,
    partner_id: str,
    settlement_date: date | None,
    ingestion_run_id: str,
    file_name: str,
) -> str:
    """Build a run-addressed settlement quarantine key."""
    _validate_partition_value(partner_id, "partner_id")
    _validate_partition_value(ingestion_run_id, "ingestion_run_id")
    date_value = settlement_date.isoformat() if settlement_date else "unknown"
    return (
        f"{SETTLEMENT_PREFIX}/partner_id={partner_id}/settlement_date={date_value}/"
        f"ingestion_run_id={ingestion_run_id}/{_safe_file_name(file_name)}"
    )


class SettlementObjectStorage:
    """Map settlement domain operations to a backend and two private buckets."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        bronze_bucket: str,
        quarantine_bucket: str,
    ) -> None:
        self.backend = backend
        self.bronze_bucket = bronze_bucket
        self.quarantine_bucket = quarantine_bucket

    def copy_to_bronze(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        ingestion_run_id: str,
        metadata: Mapping[str, object],
    ) -> str:
        """Write unchanged CSV bytes to the immutable Bronze object key."""
        object_key = build_bronze_object_key(
            partner_id=partner_id,
            settlement_date=settlement_date,
            ingestion_date=ingestion_date,
            checksum_sha256=checksum_sha256,
            file_name=source.name,
        )
        stored = self.backend.put_immutable(
            bucket=self.bronze_bucket,
            object_key=object_key,
            source=source,
            checksum_sha256=checksum_sha256,
            content_type="text/csv",
            metadata={**metadata, "artifact_type": "settlement_raw"},
        )
        return stored.uri

    def quarantine_file(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date | None,
        ingestion_run_id: str,
        checksum_sha256: str,
        metadata: Mapping[str, object],
    ) -> str:
        """Write an invalid raw source to a run-addressed quarantine key."""
        object_key = build_quarantine_object_key(
            partner_id=partner_id,
            settlement_date=settlement_date,
            ingestion_run_id=ingestion_run_id,
            file_name=source.name,
        )
        stored = self.backend.put_immutable(
            bucket=self.quarantine_bucket,
            object_key=object_key,
            source=source,
            checksum_sha256=checksum_sha256,
            content_type="text/csv",
            metadata={**metadata, "artifact_type": "quarantined_raw"},
        )
        return stored.uri

    def write_rejected_records(
        self,
        records: Sequence[RejectedRecord],
        *,
        source_file_name: str,
        partner_id: str,
        settlement_date: date,
        ingestion_run_id: str,
        metadata: Mapping[str, object],
    ) -> str:
        """Write deterministic rejected-record evidence as immutable JSON Lines."""
        object_key = build_quarantine_object_key(
            partner_id=partner_id,
            settlement_date=settlement_date,
            ingestion_run_id=ingestion_run_id,
            file_name=f"{source_file_name}.rejected.jsonl",
        )
        content = "".join(
            f"{json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False)}\n"
            for record in records
        ).encode()
        stored = self.backend.put_bytes_immutable(
            bucket=self.quarantine_bucket,
            object_key=object_key,
            data=content,
            content_type="application/x-ndjson",
            metadata={**metadata, "artifact_type": "rejected_records"},
        )
        return stored.uri


class LocalSettlementStorage(SettlementObjectStorage):
    """Compatibility wrapper retaining the Phase 2 local constructor."""

    def __init__(self, bronze_root: Path, quarantine_root: Path) -> None:
        self.bronze_root = bronze_root
        self.quarantine_root = quarantine_root
        backend = LocalStorageBackend(
            {
                LOCAL_BRONZE_BUCKET: bronze_root,
                LOCAL_QUARANTINE_BUCKET: quarantine_root,
            }
        )
        super().__init__(
            backend,
            bronze_bucket=LOCAL_BRONZE_BUCKET,
            quarantine_bucket=LOCAL_QUARANTINE_BUCKET,
        )

    def bronze_path(
        self,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        file_name: str,
    ) -> Path:
        """Return the local path for a deterministic Bronze object key."""
        key = build_bronze_object_key(
            partner_id=partner_id,
            settlement_date=settlement_date,
            ingestion_date=ingestion_date,
            checksum_sha256=checksum_sha256,
            file_name=file_name,
        )
        return Path(self.backend.build_uri(self.bronze_bucket, key))


def _safe_file_name(file_name: str) -> str:
    if not file_name or Path(file_name).name != file_name or "/" in file_name or "\\" in file_name:
        raise ValueError("file_name must not contain directory segments")
    return file_name


def _validate_partition_value(value: str, name: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
        raise ValueError(f"{name} contains unsafe object-key characters")
