from __future__ import annotations

from uuid import uuid4

import pytest

from orchestration import tasks
from orchestration.config import validate_backfill_params
from orchestration.tasks import process_silver, register_backfill_request


def test_backfill_request_validation_and_dry_run_is_write_free() -> None:
    request_id = uuid4()
    params = {
        "request_id": str(request_id),
        "source_type": "CDC",
        "entity": "customers",
        "input_prefix": "cdc/entity=customers/",
        "from_date": "2026-07-01",
        "to_date": "2026-07-22",
        "force_reprocess": False,
        "dry_run": True,
    }

    request = validate_backfill_params(params)
    result = register_backfill_request(
        params=params,
        airflow_run_id="manual__safe-dry-run",
        requested_by="unit-test",
        environment={},
    )

    assert request.request_id == request_id
    assert result["control_written"] is False
    assert result["dry_run"] is True


@pytest.mark.parametrize(
    "updates",
    [
        {"request_id": "not-a-uuid"},
        {"source_type": "SHELL"},
        {"entity": "customers; rm -rf /"},
        {"input_prefix": "../secrets"},
        {"from_date": "2026-07-23", "to_date": "2026-07-01"},
    ],
)
def test_unsafe_backfill_params_are_rejected(updates: dict[str, object]) -> None:
    params: dict[str, object] = {
        "request_id": str(uuid4()),
        "source_type": "CDC",
        "dry_run": True,
    }
    params.update(updates)

    with pytest.raises(ValueError):
        validate_backfill_params(params)


def test_dry_run_never_advertises_new_output_assets(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks,
        "_run_application_cli",
        lambda *args, **kwargs: (
            [
                {
                    "discovered": 1,
                    "results": [
                        {
                            "status": "COMPLETED",
                            "input_record_count": 10,
                            "output_record_count": 10,
                            "input_object_uri": "s3://bronze/input",
                            "output_object_uris": ["s3://silver/existing-output"],
                            "skipped": True,
                        }
                    ],
                }
            ],
            0,
        ),
    )

    result = process_silver(
        source_type="CDC",
        storage_backend="minio",
        entity="customers",
        dry_run=True,
    )

    assert result["records_written"] == 0
    assert result["output_assets"] == ()
