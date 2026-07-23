from __future__ import annotations

import os

import pytest

from orchestration.config import ControlDatabaseSettings
from orchestration.control import ControlStore


@pytest.fixture(autouse=True)
def require_airflow_integration() -> None:
    if os.getenv("RUN_AIRFLOW_INTEGRATION") != "1":
        pytest.skip("Set RUN_AIRFLOW_INTEGRATION=1 and initialize the Airflow control database")


@pytest.fixture
def control_store() -> ControlStore:
    return ControlStore(ControlDatabaseSettings.from_env(os.environ))
