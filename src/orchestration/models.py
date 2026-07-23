"""Typed orchestration state shared by DAG tasks and the control store."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


class PipelineStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"


class PipelineRunType(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"
    RECOVERY = "RECOVERY"


class QualityClassification(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class TaskResult:
    status: PipelineStatus
    records_read: int = 0
    records_written: int = 0
    records_rejected: int = 0
    input_assets: tuple[str, ...] = ()
    output_assets: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    def to_xcom(self) -> dict[str, Any]:
        """Return only small operational metadata; never return record payloads."""
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class QualityResult:
    rule_name: str
    classification: QualityClassification
    observed_value: float | None
    warn_threshold: float | None
    fail_threshold: float | None
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["classification"] = self.classification.value
        return payload


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    request_id: UUID
    source_type: str
    entity_name: str | None
    input_prefix: str | None
    from_date: date | None
    to_date: date | None
    force_reprocess: bool
    dry_run: bool


def deterministic_pipeline_run_id(dag_id: str, airflow_run_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"fintech-platform:{dag_id}:{airflow_run_id}")


def safe_task_run_id(pipeline_run_id: UUID, task_id: str, try_number: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"{pipeline_run_id}:{task_id}:{try_number}")


def compact_asset_id(uri: str) -> str:
    """Hash an asset URI for logs when exposing the URI itself is unnecessary."""
    return hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
