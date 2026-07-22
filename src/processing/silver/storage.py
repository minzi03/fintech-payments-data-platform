"""Immutable Silver object layouts over the shared storage boundary."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

from common.storage import StorageBackend, StoredObject
from processing.silver.models import OutputType, SilverOutput, deterministic_part_id
from processing.silver.parquet import SerializedSilver, read_table_bytes


def build_silver_object_key(
    *,
    output_type: OutputType,
    entity_name: str,
    run_id: str,
    processing_date: date,
    event_date: date | None = None,
    partner_id: str | None = None,
    settlement_date: date | None = None,
) -> str:
    part = deterministic_part_id(run_id, output_type, entity_name)
    if output_type is OutputType.HISTORY:
        return (
            f"silver/cdc/history/entity={entity_name}/event_date={event_date or processing_date}/"
            f"processing_date={processing_date}/run_id={run_id}/part-{part}.parquet"
        )
    if output_type in {OutputType.LATEST_ALL, OutputType.CURRENT}:
        return (
            f"silver/cdc/{output_type.value}/entity={entity_name}/snapshot_date={processing_date}/"
            f"run_id={run_id}/part-{part}.parquet"
        )
    if output_type is OutputType.EVENTS:
        return (
            f"silver/cdc/events/entity={entity_name}/event_date={event_date or processing_date}/"
            f"processing_date={processing_date}/run_id={run_id}/part-{part}.parquet"
        )
    if output_type is OutputType.SETTLEMENTS:
        return (
            f"silver/settlements/partner_id={partner_id or 'UNKNOWN'}/"
            f"settlement_date={settlement_date or processing_date}/run_id={run_id}/"
            f"part-{part}.parquet"
        )
    if output_type is OutputType.REJECTIONS:
        return (
            f"silver/rejections/source={entity_name.split(':', 1)[0]}/"
            f"entity={entity_name.split(':', 1)[-1]}/processing_date={processing_date}/"
            f"run_id={run_id}/rejections.parquet"
        )
    return (
        f"silver/unresolved_references/entity={entity_name}/processing_date={processing_date}/"
        f"run_id={run_id}/part-{part}.parquet"
    )


class SilverStorage:
    def __init__(self, backend: StorageBackend, *, bucket: str) -> None:
        self.backend = backend
        self.bucket = bucket

    def put(
        self,
        *,
        serialized: SerializedSilver,
        object_key: str,
        output_type: OutputType,
        entity_name: str,
        run_id: str,
        input_checksum: str,
        code_version: str,
        source_schema_version: str,
        silver_schema_version: str,
        processed_at: datetime,
    ) -> SilverOutput:
        stored = self.backend.put_immutable(
            bucket=self.bucket,
            object_key=object_key,
            source=serialized.path,
            checksum_sha256=serialized.checksum_sha256,
            content_type="application/vnd.apache.parquet",
            metadata={
                "artifact_type": "silver_parquet",
                "pipeline_name": "bronze-to-silver",
                "source_type": "bronze",
                "entity_name": entity_name,
                "output_type": output_type.value,
                "processing_run_id": run_id,
                "processing_date": processed_at.date().isoformat(),
                "record_count": serialized.record_count,
                "input_checksum": input_checksum,
                "code_version": code_version,
                "source_schema_version": source_schema_version,
                "silver_schema_version": silver_schema_version,
            },
        )
        if stored.checksum_sha256 != serialized.checksum_sha256:
            raise RuntimeError("Silver object checksum verification failed")
        return SilverOutput(
            output_type=output_type,
            object_uri=stored.uri,
            checksum_sha256=stored.checksum_sha256,
            record_count=serialized.record_count,
        )

    def read_table(self, object_uri: str, schema: pa.Schema | None = None) -> pa.Table:
        if object_uri.startswith("s3://"):
            bucket, key = _parse_object_uri(object_uri, self.bucket)
            payload = self.backend.read_bytes(bucket, key)
        else:
            payload = Path(object_uri).read_bytes()
        return read_table_bytes(payload, schema)

    def stat(self, object_uri: str) -> StoredObject | None:
        if not object_uri.startswith("s3://"):
            return None
        bucket, key = _parse_object_uri(object_uri, self.bucket)
        return self.backend.stat(bucket, key)


def _parse_object_uri(uri: str, default_bucket: str) -> tuple[str, str]:
    prefix = f"s3://{default_bucket}/"
    if uri.startswith(prefix):
        return default_bucket, uri.removeprefix(prefix)
    raise ValueError("Silver object URI must use the configured s3:// bucket")
