"""Thin orchestration adapters around the existing Phase 2-6 applications."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from ingestion.batch.discovery import discover_files
from ingestion.batch.storage_factory import create_storage_backend
from orchestration.config import ControlDatabaseSettings, validate_backfill_params
from orchestration.control import ControlStore
from orchestration.models import (
    PipelineRunType,
    PipelineStatus,
    TaskResult,
    deterministic_pipeline_run_id,
)
from orchestration.quality import (
    classify_threshold,
    pipeline_status_from_quality,
    rejection_rate_result,
)
from processing.silver.config import SilverSettings
from processing.silver.discovery import BronzeDiscovery
from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.models import SourceType
from processing.silver.processor import CDC_PIPELINE, SETTLEMENT_PIPELINE


def begin_pipeline(
    *,
    dag_id: str,
    airflow_run_id: str,
    pipeline_name: str,
    logical_date: datetime,
    run_type: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environment is None else environment
    store = ControlStore(ControlDatabaseSettings.from_env(env))
    pipeline_run_id = deterministic_pipeline_run_id(dag_id, airflow_run_id)
    store.register_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        dag_id=dag_id,
        airflow_run_id=airflow_run_id,
        pipeline_name=pipeline_name,
        logical_date=_utc(logical_date),
        run_type=PipelineRunType(run_type),
    )
    store.mark_pipeline_running(pipeline_run_id)
    return str(pipeline_run_id)


def record_task_result(
    *,
    pipeline_run_id: str,
    task_id: str,
    result: Mapping[str, object],
    try_number: int = 1,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    store = ControlStore(ControlDatabaseSettings.from_env(env))
    metadata = dict(result.get("metadata") or {})
    row = store.record_task_run(
        pipeline_run_id=UUID(pipeline_run_id),
        task_id=task_id,
        try_number=try_number,
        status=PipelineStatus(str(result.get("status", "SUCCEEDED"))),
        records_read=int(result.get("records_read", 0)),
        records_written=int(result.get("records_written", 0)),
        records_rejected=int(result.get("records_rejected", 0)),
        result_metadata=metadata,
    )
    return {"task_run_id": str(row["task_run_id"]), "status": str(row["status"])}


def finish_pipeline(
    *,
    pipeline_run_id: str,
    results: Sequence[Mapping[str, object]],
    warn_rate: float,
    fail_rate: float,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    records_read = sum(int(item.get("records_read", 0)) for item in results)
    records_written = sum(int(item.get("records_written", 0)) for item in results)
    records_rejected = sum(int(item.get("records_rejected", 0)) for item in results)
    output_assets = sorted(
        {str(asset) for item in results for asset in (item.get("output_assets") or [])}
    )
    quality = rejection_rate_result(
        records_read=records_read,
        records_rejected=records_rejected,
        warn_rate=warn_rate,
        fail_rate=fail_rate,
    )
    status = PipelineStatus(pipeline_status_from_quality([quality]))
    store = ControlStore(ControlDatabaseSettings.from_env(env))
    store.record_quality_results(UUID(pipeline_run_id), [quality])
    store.complete_pipeline(
        UUID(pipeline_run_id),
        status=status,
        records_read=records_read,
        records_written=records_written,
        records_rejected=records_rejected,
        output_assets=output_assets,
    )
    if status is PipelineStatus.FAILED:
        raise RuntimeError("Orchestration quality gate failed")
    return {
        "pipeline_run_id": pipeline_run_id,
        "status": status.value,
        "quality": quality.to_dict(),
        "records_read": records_read,
        "records_written": records_written,
        "records_rejected": records_rejected,
    }


def finish_cdc_health_pipeline(
    *,
    pipeline_run_id: str,
    total_lag: int,
    freshness_seconds: float,
    lag_warn: int,
    lag_fail: int,
    freshness_warn: int,
    freshness_fail: int,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    quality = [
        classify_threshold(
            rule_name="cdc_consumer_group_lag",
            observed_value=float(total_lag),
            warn_threshold=float(lag_warn),
            fail_threshold=float(lag_fail),
        ),
        classify_threshold(
            rule_name="cdc_manifest_freshness_seconds",
            observed_value=freshness_seconds,
            warn_threshold=float(freshness_warn),
            fail_threshold=float(freshness_fail),
        ),
    ]
    status = PipelineStatus(pipeline_status_from_quality(quality))
    store = ControlStore(ControlDatabaseSettings.from_env(env))
    store.record_quality_results(UUID(pipeline_run_id), quality)
    store.complete_pipeline(
        UUID(pipeline_run_id),
        status=status,
        records_read=0,
        records_written=0,
        records_rejected=0,
    )
    if status is PipelineStatus.FAILED:
        raise RuntimeError("CDC health quality gate failed")
    return {
        "pipeline_run_id": pipeline_run_id,
        "status": status.value,
        "quality": [item.to_dict() for item in quality],
    }


def discover_settlement_inputs(input_dir: str) -> dict[str, object]:
    paths = discover_files(file=None, input_dir=Path(input_dir))
    return TaskResult(
        status=PipelineStatus.SKIPPED if not paths else PipelineStatus.SUCCEEDED,
        records_read=len(paths),
        input_assets=tuple(str(path) for path in paths),
        metadata={"file_count": len(paths)},
    ).to_xcom()


def ingest_settlements(
    *,
    input_dir: str,
    partner_id: str,
    contract: str,
    storage_backend: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    payloads, return_code = _run_application_cli(
        "ingestion.batch.cli",
        [
            "ingest-settlements",
            "--input-dir",
            input_dir,
            "--partner-id",
            partner_id,
            "--contract",
            contract,
            "--storage-backend",
            storage_backend,
        ],
        environment=environment,
        accepted_return_codes={0, 1},
    )
    processed = [item for item in payloads if item.get("status") == "PROCESSED"]
    rejected = [item for item in payloads if item.get("status") in {"QUARANTINED", "FAILED"}]
    outputs = tuple(
        str(value)
        for item in payloads
        for value in (item.get("bronze_path"), item.get("quarantine_path"))
        if value
    )
    return TaskResult(
        status=(PipelineStatus.PARTIAL if return_code else PipelineStatus.SUCCEEDED),
        records_read=sum(int(item.get("record_count", 0)) for item in payloads),
        records_written=sum(int(item.get("accepted_count", 0)) for item in processed),
        records_rejected=sum(int(item.get("rejected_count", 0)) for item in payloads),
        output_assets=outputs,
        metadata={
            "file_count": len(payloads),
            "processed_files": len(processed),
            "quarantined_or_failed_files": len(rejected),
        },
    ).to_xcom()


def validate_ingestion_result(result: Mapping[str, object]) -> dict[str, object]:
    metadata = dict(result.get("metadata") or {})
    if int(metadata.get("file_count", 0)) == 0:
        return TaskResult(status=PipelineStatus.SKIPPED).to_xcom()
    if int(metadata.get("processed_files", 0)) == 0:
        raise RuntimeError("No settlement file reached immutable Bronze storage")
    return dict(result)


def process_silver(
    *,
    source_type: str,
    storage_backend: str,
    input_prefix: str | None = None,
    entity: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    force_reprocess: bool = False,
    dry_run: bool = False,
    max_objects: int = 100,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    command = "process-cdc" if source_type.upper() == "CDC" else "process-settlements"
    arguments = [command, "--storage-backend", storage_backend, "--max-objects", str(max_objects)]
    if input_prefix:
        arguments.extend(("--input-prefix", input_prefix))
    if entity:
        arguments.extend(("--entity", entity))
    if from_date:
        arguments.extend(("--from-date", from_date.isoformat()))
    if to_date:
        arguments.extend(("--to-date", to_date.isoformat()))
    if force_reprocess:
        arguments.append("--force-reprocess")
    if dry_run:
        arguments.append("--dry-run")
    payloads, _ = _run_application_cli(
        "processing.silver.cli", arguments, environment=environment, accepted_return_codes={0}
    )
    payload = payloads[-1] if payloads else {"discovered": 0, "results": []}
    results = list(payload.get("results") or [])
    return TaskResult(
        status=PipelineStatus.SKIPPED if not results else PipelineStatus.SUCCEEDED,
        records_read=sum(int(item.get("input_record_count", 0)) for item in results),
        records_written=(
            0 if dry_run else sum(int(item.get("output_record_count", 0)) for item in results)
        ),
        records_rejected=sum(int(item.get("rejected_record_count", 0)) for item in results),
        input_assets=tuple(str(item.get("input_object_uri")) for item in results),
        output_assets=(
            ()
            if dry_run
            else tuple(
                str(uri) for item in results for uri in (item.get("output_object_uris") or [])
            )
        ),
        metadata={
            "discovered_objects": int(payload.get("discovered", 0)),
            "completed_runs": sum(item.get("status") == "COMPLETED" for item in results),
            "skipped_runs": sum(bool(item.get("skipped")) for item in results),
            "dry_run": dry_run,
        },
    ).to_xcom()


def discover_unprocessed_bronze(
    *,
    source_type: str,
    storage_backend: str,
    entity: str | None = None,
    input_prefix: str | None = None,
    max_objects: int = 100,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env = dict(os.environ if environment is None else environment)
    settings = SilverSettings.from_env(env, backend_override=storage_backend)
    backend = create_storage_backend(settings.storage)
    discovery = BronzeDiscovery(backend, settings.storage)
    manifest = SqliteProcessingManifest(settings.manifest_path)
    kind = SourceType(source_type.upper())
    pipeline = CDC_PIPELINE if kind is SourceType.CDC else SETTLEMENT_PIPELINE
    inputs = discovery.discover(
        source_type=kind,
        input_prefix=input_prefix,
        entity=entity,
        max_objects=max_objects,
    )
    pending = [
        item
        for item in inputs
        if manifest.find_latest_identity(
            pipeline_name=pipeline,
            input_checksum=item.checksum_sha256,
            code_version=settings.code_version,
            schema_version=settings.silver_schema_version,
        )
        is None
    ]
    return TaskResult(
        status=PipelineStatus.SKIPPED if not pending else PipelineStatus.SUCCEEDED,
        records_read=len(inputs),
        input_assets=tuple(item.uri for item in pending),
        metadata={"discovered_objects": len(inputs), "unprocessed_objects": len(pending)},
    ).to_xcom()


def register_backfill_request(
    *,
    params: Mapping[str, object],
    airflow_run_id: str,
    requested_by: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    request = validate_backfill_params(params)
    pipeline_name = f"{request.source_type.lower()}-silver-backfill"
    if not request.dry_run:
        store = ControlStore(ControlDatabaseSettings.from_env(env))
        store.register_backfill(
            request,
            pipeline_name=pipeline_name,
            requested_by=requested_by[:128],
            airflow_run_id=airflow_run_id,
        )
    return {
        "request_id": str(request.request_id),
        "source_type": request.source_type,
        "entity": request.entity_name,
        "input_prefix": request.input_prefix,
        "from_date": request.from_date.isoformat() if request.from_date else None,
        "to_date": request.to_date.isoformat() if request.to_date else None,
        "force_reprocess": request.force_reprocess,
        "dry_run": request.dry_run,
        "control_written": not request.dry_run,
    }


def _run_application_cli(
    module: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    accepted_return_codes: set[int],
) -> tuple[list[dict[str, object]], int]:
    env = dict(os.environ if environment is None else environment)
    completed = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=int(env.get("AIRFLOW_TASK_TIMEOUT_SECONDS", "1800")),
        env=env,
    )
    if completed.returncode not in accepted_return_codes:
        error_type = "ApplicationCommandError"
        raise RuntimeError(f"{module} failed with exit code {completed.returncode}: {error_type}")
    payloads = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line:
            payloads.append(json.loads(line))
    return payloads, completed.returncode


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("logical_date must be timezone-aware")
    return value.astimezone(UTC)
