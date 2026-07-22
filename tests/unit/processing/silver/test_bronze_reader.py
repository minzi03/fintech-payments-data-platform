"""CDC Bronze reader contract tests."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common.storage import LocalStorageBackend, sha256_file
from processing.silver.bronze_reader import BronzeReader, BronzeReadError
from processing.silver.models import InputObject, QualityCode

from .conftest import bronze_row, customer_payload, write_bronze


def test_reader_validates_explicit_phase_five_schema(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    item = write_bronze(
        root, [bronze_row("customers", "c-1", customer_payload("c-1"))], entity="customers"
    )
    reader = BronzeReader(LocalStorageBackend({"fintech-bronze": root}))

    result = reader.read_cdc(item, supported_schema_version="cdc-bronze-v1")

    assert result.table.num_rows == 1
    assert result.exact_checksum == item.checksum_sha256


def test_reader_rejects_missing_columns_and_unsupported_version(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    path = root / "cdc/bad.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"event_id": ["x"]}), path)
    item = InputObject(
        str(path), "fintech-bronze", "cdc/bad.parquet", sha256_file(path), path.stat().st_size, {}
    )
    reader = BronzeReader(LocalStorageBackend({"fintech-bronze": root}))

    with pytest.raises(BronzeReadError) as captured:
        reader.read_cdc(item, supported_schema_version="cdc-bronze-v1")
    assert captured.value.code is QualityCode.INVALID_BRONZE_SCHEMA

    valid = write_bronze(
        root,
        [bronze_row("customers", "c-2", customer_payload("c-2"))],
        entity="customers",
        name="valid.parquet",
    )
    with pytest.raises(BronzeReadError) as version:
        reader.read_cdc(valid, supported_schema_version="other")
    assert version.value.code is QualityCode.SCHEMA_VERSION_UNSUPPORTED
