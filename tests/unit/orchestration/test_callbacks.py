from __future__ import annotations

from orchestration import callbacks
from orchestration.callbacks import redact_context


def test_failure_context_redacts_secrets_payloads_and_exception_values() -> None:
    safe = redact_context(
        {
            "dag_id": "settlement_batch_pipeline",
            "run_id": "scheduled__2026-07-23",
            "password": "do-not-log",
            "raw_payload": {"email": "customer@example.test"},
            "exception": RuntimeError("connection contained a secret"),
        }
    )

    assert safe["dag_id"] == "settlement_batch_pipeline"
    assert safe["password"] == "[REDACTED]"
    assert safe["raw_payload"] == "[REDACTED]"
    assert safe["exception_type"] == "RuntimeError"
    assert "do-not-log" not in str(safe)
    assert "customer@example.test" not in str(safe)


def test_failure_callback_persists_only_redacted_failure(monkeypatch) -> None:
    calls = []

    class FakeStore:
        def record_task_run(self, **kwargs):
            calls.append(("task", kwargs))

        def complete_pipeline(self, *args, **kwargs):
            calls.append(("pipeline", kwargs))

    monkeypatch.setattr(
        "orchestration.config.ControlDatabaseSettings.from_env",
        lambda environment: object(),
    )
    monkeypatch.setattr("orchestration.control.ControlStore", lambda settings: FakeStore())
    task_instance = type(
        "TaskInstance",
        (),
        {"dag_id": "dag", "task_id": "task", "try_number": 1},
    )()
    dag_run = type("DagRun", (), {"run_id": "manual__run"})()

    callbacks.failure_callback(
        {
            "task_instance": task_instance,
            "dag_run": dag_run,
            "logical_date": "2026-07-23T00:00:00Z",
            "exception": RuntimeError("password=not-for-storage"),
        }
    )

    assert [name for name, _ in calls] == ["task", "pipeline"]
    assert "not-for-storage" not in str(calls)
