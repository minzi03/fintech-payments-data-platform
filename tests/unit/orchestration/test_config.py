from __future__ import annotations

import pytest

from common.config import ConfigurationError
from orchestration.config import ControlDatabaseSettings, OrchestrationSettings


def test_orchestration_defaults_are_bounded_and_utc() -> None:
    settings = OrchestrationSettings.from_env({})

    assert settings.timezone == "UTC"
    assert settings.task_retries == 2
    assert settings.settlement_warn_rate < settings.settlement_fail_rate
    assert settings.cdc_lag_warn < settings.cdc_lag_fail


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SETTLEMENT_REJECTION_WARN_RATE", "-0.1"),
        ("SILVER_REJECTION_FAIL_RATE", "1.1"),
        ("CDC_LAG_WARN_THRESHOLD", "0"),
        ("AIRFLOW_TASK_RETRIES", "11"),
    ],
)
def test_invalid_threshold_configuration_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        OrchestrationSettings.from_env({name: value})


def test_control_connection_repr_and_label_do_not_expose_password() -> None:
    settings = ControlDatabaseSettings.from_env(
        {"AIRFLOW_CONN_CONTROL_DB": "postgresql://control:sensitive@db:5432/airflow"}
    )

    assert "sensitive" not in repr(settings)
    assert settings.connection_label == "db:5432/airflow"
