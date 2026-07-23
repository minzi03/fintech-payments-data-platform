"""PostgreSQL-backed central orchestration control store."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from orchestration.config import ControlDatabaseSettings
from orchestration.models import (
    BackfillRequest,
    PipelineRunType,
    PipelineStatus,
    QualityResult,
    safe_task_run_id,
)


class ControlStore:
    """Persist cross-pipeline state without replacing component manifests."""

    def __init__(self, settings: ControlDatabaseSettings) -> None:
        self._settings = settings

    def register_pipeline_run(
        self,
        *,
        pipeline_run_id: UUID,
        dag_id: str,
        airflow_run_id: str,
        pipeline_name: str,
        logical_date: datetime,
        run_type: PipelineRunType,
        status: PipelineStatus = PipelineStatus.QUEUED,
        input_assets: Iterable[str] = (),
    ) -> dict[str, Any]:
        query = """
            INSERT INTO control.pipeline_runs (
                pipeline_run_id, dag_id, airflow_run_id, pipeline_name, logical_date,
                run_type, status, input_assets
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (dag_id, airflow_run_id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
        """
        return self._one(
            query,
            (
                pipeline_run_id,
                dag_id,
                airflow_run_id,
                pipeline_name,
                logical_date,
                run_type.value,
                status.value,
                _json(list(input_assets)),
            ),
        )

    def mark_pipeline_running(self, pipeline_run_id: UUID) -> dict[str, Any]:
        return self._transition_pipeline(
            pipeline_run_id,
            PipelineStatus.RUNNING,
            "started_at = COALESCE(started_at, CURRENT_TIMESTAMP)",
        )

    def complete_pipeline(
        self,
        pipeline_run_id: UUID,
        *,
        status: PipelineStatus,
        records_read: int,
        records_written: int,
        records_rejected: int,
        output_assets: Iterable[str] = (),
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in {
            PipelineStatus.SUCCEEDED,
            PipelineStatus.FAILED,
            PipelineStatus.PARTIAL,
            PipelineStatus.SKIPPED,
        }:
            raise ValueError("Pipeline completion requires a terminal status")
        return self._one(
            """
            UPDATE control.pipeline_runs
            SET status = %s, completed_at = CURRENT_TIMESTAMP,
                records_read = %s, records_written = %s, records_rejected = %s,
                output_assets = %s::jsonb, error_code = %s, error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE pipeline_run_id = %s
            RETURNING *
            """,
            (
                status.value,
                records_read,
                records_written,
                records_rejected,
                _json(list(output_assets)),
                error_code,
                _safe_error(error_message),
                pipeline_run_id,
            ),
        )

    def record_task_run(
        self,
        *,
        pipeline_run_id: UUID,
        task_id: str,
        try_number: int,
        status: PipelineStatus,
        records_read: int = 0,
        records_written: int = 0,
        records_rejected: int = 0,
        result_metadata: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        task_run_id = safe_task_run_id(pipeline_run_id, task_id, try_number)
        completed_at = (
            datetime.now(UTC)
            if status
            in {
                PipelineStatus.SUCCEEDED,
                PipelineStatus.FAILED,
                PipelineStatus.PARTIAL,
                PipelineStatus.SKIPPED,
            }
            else None
        )
        return self._one(
            """
            INSERT INTO control.task_runs (
                task_run_id, pipeline_run_id, task_id, try_number, status,
                started_at, completed_at, records_read, records_written, records_rejected,
                result_metadata, error_code, error_message
            ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s,
                      %s::jsonb, %s, %s)
            ON CONFLICT (pipeline_run_id, task_id, try_number) DO UPDATE SET
                status = EXCLUDED.status,
                completed_at = EXCLUDED.completed_at,
                records_read = EXCLUDED.records_read,
                records_written = EXCLUDED.records_written,
                records_rejected = EXCLUDED.records_rejected,
                result_metadata = EXCLUDED.result_metadata,
                error_code = EXCLUDED.error_code,
                error_message = EXCLUDED.error_message,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                task_run_id,
                pipeline_run_id,
                task_id,
                try_number,
                status.value,
                completed_at,
                records_read,
                records_written,
                records_rejected,
                _json(result_metadata or {}),
                error_code,
                _safe_error(error_message),
            ),
        )

    def record_quality_results(
        self, pipeline_run_id: UUID, results: Iterable[QualityResult]
    ) -> list[dict[str, Any]]:
        recorded = []
        for result in results:
            recorded.append(
                self._one(
                    """
                    INSERT INTO control.data_quality_results (
                        quality_result_id, pipeline_run_id, rule_name, classification,
                        observed_value, warn_threshold, fail_threshold, details
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (pipeline_run_id, rule_name) DO UPDATE SET
                        classification = EXCLUDED.classification,
                        observed_value = EXCLUDED.observed_value,
                        warn_threshold = EXCLUDED.warn_threshold,
                        fail_threshold = EXCLUDED.fail_threshold,
                        details = EXCLUDED.details,
                        evaluated_at = CURRENT_TIMESTAMP
                    RETURNING *
                    """,
                    (
                        uuid4(),
                        pipeline_run_id,
                        result.rule_name,
                        result.classification.value,
                        result.observed_value,
                        result.warn_threshold,
                        result.fail_threshold,
                        _json(result.details),
                    ),
                )
            )
        return recorded

    def register_backfill(
        self,
        request: BackfillRequest,
        *,
        pipeline_name: str,
        requested_by: str,
        airflow_run_id: str,
    ) -> dict[str, Any]:
        return self._one(
            """
            INSERT INTO control.backfill_requests (
                request_id, pipeline_name, source_type, requested_by, entity_name,
                input_prefix, from_date, to_date, force_reprocess, dry_run, status,
                airflow_run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED', %s)
            ON CONFLICT (request_id) DO UPDATE SET airflow_run_id = EXCLUDED.airflow_run_id
            RETURNING *
            """,
            (
                request.request_id,
                pipeline_name,
                request.source_type,
                requested_by,
                request.entity_name,
                request.input_prefix,
                request.from_date,
                request.to_date,
                request.force_reprocess,
                request.dry_run,
                airflow_run_id,
            ),
        )

    def table_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in (
            "pipeline_runs",
            "task_runs",
            "data_quality_results",
            "backfill_requests",
        ):
            row = self._one(f"SELECT count(*) AS count FROM control.{table}", ())
            result[table] = int(row["count"])
        return result

    def get_pipeline_run(self, pipeline_run_id: UUID) -> dict[str, Any] | None:
        with psycopg.connect(self._settings.connection_uri, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM control.pipeline_runs WHERE pipeline_run_id = %s",
                (pipeline_run_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _transition_pipeline(
        self, pipeline_run_id: UUID, status: PipelineStatus, timestamp_assignment: str
    ) -> dict[str, Any]:
        return self._one(
            f"""
            UPDATE control.pipeline_runs
            SET status = %s, {timestamp_assignment}, updated_at = CURRENT_TIMESTAMP
            WHERE pipeline_run_id = %s
            RETURNING *
            """,
            (status.value, pipeline_run_id),
        )

    def _one(self, query: str, parameters: tuple[object, ...]) -> dict[str, Any]:
        with psycopg.connect(self._settings.connection_uri, row_factory=dict_row) as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            raise RuntimeError("Control-store statement did not return a row")
        return dict(row)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _safe_error(message: str | None) -> str | None:
    if message is None:
        return None
    return message.replace("\n", " ")[:1000]
