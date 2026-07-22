"""Silver typed configuration tests."""

import pytest

from common.config import ConfigurationError
from processing.silver.config import SilverSettings


def test_silver_defaults_are_local_and_versioned() -> None:
    settings = SilverSettings.from_env({})

    assert settings.storage.silver_bucket == "fintech-silver"
    assert settings.code_version == "phase6-v1"
    assert settings.silver_schema_version == "silver-v1"
    assert settings.max_objects == 100


@pytest.mark.parametrize("value", ["0", "10001", "not-int"])
def test_max_objects_is_bounded(value: str) -> None:
    with pytest.raises(ConfigurationError):
        SilverSettings.from_env({"SILVER_MAX_OBJECTS": value})


def test_unsafe_schema_version_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="unsafe"):
        SilverSettings.from_env({"SILVER_SCHEMA_VERSION": "bad value"})
