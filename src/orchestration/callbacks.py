"""PII- and secret-safe failure callback helpers."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any

LOGGER = logging.getLogger(__name__)
_SENSITIVE = re.compile(r"password|secret|token|key|authorization|payload", re.IGNORECASE)


def redact_context(context: Mapping[str, Any]) -> dict[str, str]:
    """Allowlist non-sensitive identifiers and redact suspicious values."""
    safe: dict[str, str] = {}
    for name in ("dag_id", "task_id", "run_id", "try_number", "logical_date"):
        value = context.get(name)
        if value is not None:
            safe[name] = str(value)[:256]
    for name in context:
        if _SENSITIVE.search(str(name)):
            safe[str(name)] = "[REDACTED]"
    exception = context.get("exception")
    if exception is not None:
        safe["exception_type"] = type(exception).__name__
    return safe


def failure_callback(context: Mapping[str, Any]) -> None:
    """Log safe identifiers and best-effort terminal control state."""
    task_instance = context.get("task_instance")
    dag_run = context.get("dag_run")
    normalized = {
        "dag_id": getattr(task_instance, "dag_id", None),
        "task_id": getattr(task_instance, "task_id", None),
        "run_id": getattr(dag_run, "run_id", None),
        "try_number": getattr(task_instance, "try_number", None),
        "logical_date": context.get("logical_date"),
        "exception": context.get("exception"),
    }
    LOGGER.error("Airflow task failed context=%s", redact_context(normalized))
    try:
        from orchestration.config import ControlDatabaseSettings
        from orchestration.control import ControlStore
        from orchestration.models import (
            PipelineStatus,
            deterministic_pipeline_run_id,
        )

        dag_id = str(normalized["dag_id"])
        run_id = str(normalized["run_id"])
        task_id = str(normalized["task_id"])
        pipeline_run_id = deterministic_pipeline_run_id(dag_id, run_id)
        store = ControlStore(ControlDatabaseSettings.from_env(os.environ))
        store.record_task_run(
            pipeline_run_id=pipeline_run_id,
            task_id=task_id,
            try_number=int(normalized["try_number"] or 1),
            status=PipelineStatus.FAILED,
            error_code="AIRFLOW_TASK_FAILURE",
            error_message=str(normalized.get("exception_type", "TaskFailure")),
        )
        store.complete_pipeline(
            pipeline_run_id,
            status=PipelineStatus.FAILED,
            records_read=0,
            records_written=0,
            records_rejected=0,
            error_code="AIRFLOW_TASK_FAILURE",
            error_message=str(normalized.get("exception_type", "TaskFailure")),
        )
    except Exception as error:  # callback failure must not hide the original task error
        LOGGER.error("Failed to persist redacted control failure type=%s", type(error).__name__)
