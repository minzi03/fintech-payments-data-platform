"""Validated readers for Phase 5 CDC Parquet and raw settlement CSV objects."""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq

from common.storage import StorageBackend, sha256_bytes
from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.models import InputObject, QualityCode


class BronzeReadError(ValueError):
    def __init__(self, code: QualityCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReadCdcObject:
    table: pa.Table
    exact_checksum: str


class BronzeReader:
    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def read_bytes(self, input_object: InputObject) -> bytes:
        payload = self._backend.read_bytes(input_object.bucket, input_object.object_key)
        checksum = sha256_bytes(payload)
        if input_object.checksum_sha256 and checksum != input_object.checksum_sha256:
            raise BronzeReadError(
                QualityCode.INVALID_BRONZE_SCHEMA,
                "Input object checksum differs from storage metadata",
            )
        return payload

    def read_cdc(
        self,
        input_object: InputObject,
        *,
        supported_schema_version: str,
    ) -> ReadCdcObject:
        payload = self.read_bytes(input_object)
        try:
            table = pq.read_table(pa.BufferReader(payload))
        except (pa.ArrowInvalid, OSError) as error:
            raise BronzeReadError(
                QualityCode.INVALID_BRONZE_SCHEMA,
                "CDC Bronze object is not readable Parquet",
            ) from error
        expected = {field.name: field.type for field in CDC_ARROW_SCHEMA}
        actual = {field.name: field.type for field in table.schema}
        missing = sorted(set(expected) - set(actual))
        incompatible = sorted(
            name
            for name, data_type in expected.items()
            if name in actual and actual[name] != data_type
        )
        if missing or incompatible:
            detail = ", ".join(
                [
                    *(f"missing:{name}" for name in missing),
                    *(f"type:{name}" for name in incompatible),
                ]
            )
            raise BronzeReadError(
                QualityCode.INVALID_BRONZE_SCHEMA,
                f"CDC Bronze schema is incompatible ({detail})",
            )
        versions = set(table.column("schema_version").to_pylist())
        if versions != {supported_schema_version}:
            raise BronzeReadError(
                QualityCode.SCHEMA_VERSION_UNSUPPORTED,
                "CDC Bronze schema version is unsupported",
            )
        return ReadCdcObject(table=table, exact_checksum=sha256_bytes(payload))
