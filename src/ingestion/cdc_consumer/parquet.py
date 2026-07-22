"""Explicit CDC Bronze Parquet schema and deterministic serialization."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.cdc_consumer.models import CdcBatch, utc_isoformat

CDC_ARROW_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("entity_name", pa.string(), nullable=False),
        pa.field("operation", pa.string(), nullable=False),
        pa.field("is_snapshot", pa.bool_(), nullable=False),
        pa.field("is_deleted", pa.bool_(), nullable=False),
        pa.field("is_tombstone", pa.bool_(), nullable=False),
        pa.field("event_key_json", pa.large_string()),
        pa.field("before_json", pa.large_string()),
        pa.field("after_json", pa.large_string()),
        pa.field("source_metadata_json", pa.large_string()),
        pa.field("source_lsn", pa.int64()),
        pa.field("source_tx_id", pa.int64()),
        pa.field("source_ts_ms", pa.int64()),
        pa.field("connector_ts_ms", pa.int64()),
        pa.field("kafka_topic", pa.string(), nullable=False),
        pa.field("kafka_partition", pa.int32(), nullable=False),
        pa.field("kafka_offset", pa.int64(), nullable=False),
        pa.field("kafka_message_ts_ms", pa.int64()),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("raw_event_json", pa.large_string()),
    ],
    metadata={
        b"contract": b"fintech-cdc-bronze",
        b"parquet_schema_version": b"1",
        b"json_encoding": b"utf-8-canonical-where-derived",
    },
)


@dataclass(frozen=True)
class SerializedParquet:
    path: Path
    checksum_sha256: str
    size_bytes: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Parquet timestamps must be timezone-aware")
    return value.astimezone(UTC)


def records_for_batch(batch: CdcBatch, *, ingested_at: datetime) -> list[dict[str, object]]:
    stable_ingested_at = _utc(ingested_at)
    return [
        {
            "event_id": event.event_id,
            "entity_name": event.entity_name,
            "operation": event.operation,
            "is_snapshot": event.is_snapshot,
            "is_deleted": event.is_deleted,
            "is_tombstone": event.is_tombstone,
            "event_key_json": event.event_key_json,
            "before_json": event.before_json,
            "after_json": event.after_json,
            "source_metadata_json": event.source_metadata_json,
            "source_lsn": event.source_lsn,
            "source_tx_id": event.source_tx_id,
            "source_ts_ms": event.source_ts_ms,
            "connector_ts_ms": event.connector_ts_ms,
            "kafka_topic": event.kafka_topic,
            "kafka_partition": event.kafka_partition,
            "kafka_offset": event.kafka_offset,
            "kafka_message_ts_ms": event.kafka_message_ts_ms,
            "ingested_at": stable_ingested_at,
            "schema_version": event.schema_version,
            "raw_event_json": event.raw_event_json,
        }
        for event in batch.events
    ]


def serialize_batch(
    batch: CdcBatch,
    *,
    temp_dir: Path,
    ingested_at: datetime,
) -> SerializedParquet:
    """Serialize exact uploaded bytes to a temporary ZSTD Parquet file."""

    temp_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        records_for_batch(batch, ingested_at=ingested_at),
        schema=CDC_ARROW_SCHEMA,
    )
    with tempfile.NamedTemporaryFile(
        prefix=f"cdc-{batch.batch_id}-",
        suffix=".parquet",
        dir=temp_dir,
        delete=False,
    ) as handle:
        path = Path(handle.name)
    try:
        pq.write_table(
            table,
            path,
            compression="zstd",
            version="2.6",
            data_page_version="2.0",
            use_dictionary=True,
            write_statistics=True,
        )
        payload = path.read_bytes()
        return SerializedParquet(
            path=path,
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise


def cleanup_serialized(serialized: SerializedParquet | None) -> None:
    if serialized is not None:
        serialized.path.unlink(missing_ok=True)


def schema_summary() -> dict[str, str]:
    return {field.name: str(field.type) for field in CDC_ARROW_SCHEMA}


def stable_ingestion_metadata_timestamp(value: datetime) -> str:
    return utc_isoformat(_utc(value))
