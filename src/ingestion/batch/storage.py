"""Replaceable storage boundary and local immutable Bronze implementation."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .discovery import calculate_sha256
from .models import RejectedRecord


class ImmutableStorageError(RuntimeError):
    """Raised when an existing immutable destination has different content."""


class SettlementStorage(Protocol):
    """Storage operations required by ingestion, independent of object-store technology."""

    def copy_to_bronze(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        metadata: dict[str, Any],
    ) -> Path: ...

    def quarantine_file(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date | None,
        ingestion_date: date,
        checksum_sha256: str,
    ) -> Path: ...

    def write_rejected_records(
        self,
        records: Sequence[RejectedRecord],
        *,
        source_file_name: str,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
    ) -> Path: ...


class LocalSettlementStorage:
    """Atomic local filesystem implementation of immutable Bronze and quarantine writes."""

    def __init__(self, bronze_root: Path, quarantine_root: Path) -> None:
        self.bronze_root = bronze_root
        self.quarantine_root = quarantine_root

    def bronze_path(
        self,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        file_name: str,
    ) -> Path:
        """Build the deterministic partitioned Bronze object path."""
        return (
            self.bronze_root
            / f"partner_id={partner_id}"
            / f"settlement_date={settlement_date.isoformat()}"
            / f"ingestion_date={ingestion_date.isoformat()}"
            / f"checksum={checksum_sha256}"
            / file_name
        )

    def copy_to_bronze(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Copy raw source bytes without transformation and write one immutable sidecar."""
        destination = self.bronze_path(
            partner_id=partner_id,
            settlement_date=settlement_date,
            ingestion_date=ingestion_date,
            checksum_sha256=checksum_sha256,
            file_name=source.name,
        )
        self._atomic_copy(source, destination, checksum_sha256)
        self._atomic_write_json_if_absent(
            destination.with_name(f"{destination.name}.metadata.json"), metadata
        )
        return destination

    def quarantine_file(
        self,
        source: Path,
        *,
        partner_id: str,
        settlement_date: date | None,
        ingestion_date: date,
        checksum_sha256: str,
    ) -> Path:
        """Retain an invalid raw file under a content-addressed quarantine path."""
        destination = (
            self._quarantine_partition(partner_id, settlement_date, ingestion_date, checksum_sha256)
            / source.name
        )
        self._atomic_copy(source, destination, checksum_sha256)
        return destination

    def write_rejected_records(
        self,
        records: Sequence[RejectedRecord],
        *,
        source_file_name: str,
        partner_id: str,
        settlement_date: date,
        ingestion_date: date,
        checksum_sha256: str,
    ) -> Path:
        """Write rejected records as deterministic JSON Lines beside quarantine evidence."""
        destination = (
            self._quarantine_partition(partner_id, settlement_date, ingestion_date, checksum_sha256)
            / f"{source_file_name}.rejected.jsonl"
        )
        content = "".join(
            f"{json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False)}\n"
            for record in records
        )
        self._atomic_write_text_if_absent(destination, content)
        return destination

    def _quarantine_partition(
        self,
        partner_id: str,
        settlement_date: date | None,
        ingestion_date: date,
        checksum_sha256: str,
    ) -> Path:
        date_value = settlement_date.isoformat() if settlement_date else "unknown"
        return (
            self.quarantine_root
            / f"partner_id={partner_id}"
            / f"settlement_date={date_value}"
            / f"ingestion_date={ingestion_date.isoformat()}"
            / f"checksum={checksum_sha256}"
        )

    @staticmethod
    def _atomic_copy(source: Path, destination: Path, expected_checksum: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if calculate_sha256(destination) != expected_checksum:
                raise ImmutableStorageError(
                    f"Immutable destination exists with different content: {destination}"
                )
            return
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            if calculate_sha256(temporary) != expected_checksum:
                raise ImmutableStorageError("Temporary Bronze copy checksum does not match source")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _atomic_write_json_if_absent(cls, destination: Path, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        cls._atomic_write_text_if_absent(destination, content)

    @staticmethod
    def _atomic_write_text_if_absent(destination: Path, content: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
