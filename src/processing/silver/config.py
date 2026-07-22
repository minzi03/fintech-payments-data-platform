"""Typed environment configuration for Silver processing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from common.config import ConfigurationError, StorageSettings

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SilverSettings:
    storage: StorageSettings
    manifest_path: Path
    temp_dir: Path
    code_version: str
    silver_schema_version: str
    supported_bronze_schema: str
    settlement_contract_path: Path
    max_objects: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        *,
        backend_override: str | None = None,
    ) -> SilverSettings:
        code_version = environ.get("SILVER_CODE_VERSION", "phase6-v1").strip()
        schema_version = environ.get("SILVER_SCHEMA_VERSION", "silver-v1").strip()
        bronze_version = environ.get("SILVER_SUPPORTED_CDC_SCHEMA", "cdc-bronze-v1").strip()
        for name, value in (
            ("SILVER_CODE_VERSION", code_version),
            ("SILVER_SCHEMA_VERSION", schema_version),
            ("SILVER_SUPPORTED_CDC_SCHEMA", bronze_version),
        ):
            if _VERSION.fullmatch(value) is None:
                raise ConfigurationError(f"{name} contains unsafe characters")
        try:
            max_objects = int(environ.get("SILVER_MAX_OBJECTS", "100"))
        except ValueError as error:
            raise ConfigurationError("SILVER_MAX_OBJECTS must be an integer") from error
        if not 1 <= max_objects <= 10_000:
            raise ConfigurationError("SILVER_MAX_OBJECTS must be between 1 and 10000")
        return cls(
            storage=StorageSettings.from_env(environ, backend_override=backend_override),
            manifest_path=Path(
                environ.get("SILVER_MANIFEST_DB", "data/control/silver_manifest.sqlite3")
            ),
            temp_dir=Path(environ.get("SILVER_TEMP_DIR", "data/tmp/silver")),
            code_version=code_version,
            silver_schema_version=schema_version,
            supported_bronze_schema=bronze_version,
            settlement_contract_path=Path(
                environ.get("SILVER_SETTLEMENT_CONTRACT", "contracts/batch/settlement_v1.yml")
            ),
            max_objects=max_objects,
        )
