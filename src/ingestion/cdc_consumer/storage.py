"""CDC-specific immutable object layout over the shared storage interface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from common.config import StorageSettings
from common.storage import StorageBackend, StoredObject
from ingestion.batch.storage_factory import create_storage_backend
from ingestion.cdc_consumer.models import CdcBatch
from ingestion.cdc_consumer.parquet import (
    SerializedParquet,
    stable_ingestion_metadata_timestamp,
)

_PARTITION_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,249}")


@dataclass(frozen=True, slots=True)
class CdcStorageSettings:
    bronze_bucket: str
    quarantine_bucket: str


def _safe(value: str, name: str) -> str:
    if _PARTITION_VALUE.fullmatch(value) is None:
        raise ValueError(f"{name} contains unsafe object-key characters")
    return value


def build_cdc_object_key(batch: CdcBatch) -> str:
    """Build a deterministic key from one contiguous Kafka offset range."""

    return (
        f"cdc/entity={_safe(batch.entity_name, 'entity_name')}/"
        f"event_date={batch.event_date}/"
        f"topic={_safe(batch.topic, 'topic')}/"
        f"partition={batch.partition}/"
        f"offset_start={batch.offset_start}/offset_end={batch.offset_end}/"
        f"batch_id={batch.batch_id}.parquet"
    )


def build_dlq_object_key(
    *,
    dlq_name: str,
    consumer_group: str,
    topic: str,
    partition: int,
    offset: int,
    event_id: str,
) -> str:
    return (
        f"cdc-dlq/dlq={_safe(dlq_name, 'dlq_name')}/"
        f"consumer_group={_safe(consumer_group, 'consumer_group')}/"
        f"topic={_safe(topic, 'topic')}/partition={partition}/offset={offset}/"
        f"event_id={event_id}.json"
    )


class CdcObjectStorage:
    """Bind CDC Bronze and confidential DLQ operations to private buckets."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        bronze_bucket: str,
        quarantine_bucket: str,
    ) -> None:
        self.backend = backend
        self.bronze_bucket = bronze_bucket
        self.quarantine_bucket = quarantine_bucket

    def put_batch(
        self,
        batch: CdcBatch,
        serialized: SerializedParquet,
        *,
        consumer_group: str,
        ingested_at: datetime,
    ) -> StoredObject:
        object_key = build_cdc_object_key(batch)
        metadata = {
            "source_name": "postgresql-debezium-kafka",
            "artifact_type": "cdc_bronze_parquet",
            "entity_name": batch.entity_name,
            "topic": batch.topic,
            "partition": batch.partition,
            "offset_start": batch.offset_start,
            "offset_end": batch.offset_end,
            "record_count": batch.record_count,
            "schema_version": batch.schema_version,
            "consumer_group": consumer_group,
            "ingested_at": stable_ingestion_metadata_timestamp(ingested_at),
            "contains_snapshot": batch.contains_snapshot,
            "contains_delete": batch.contains_delete,
            "contains_tombstone": batch.contains_tombstone,
        }
        stored = self.backend.put_immutable(
            bucket=self.bronze_bucket,
            object_key=object_key,
            source=serialized.path,
            checksum_sha256=serialized.checksum_sha256,
            content_type="application/vnd.apache.parquet",
            metadata=metadata,
        )
        self._verify(stored, serialized.checksum_sha256, serialized.size_bytes)
        return stored

    def stat_batch(self, batch: CdcBatch) -> StoredObject | None:
        return self.backend.stat(self.bronze_bucket, build_cdc_object_key(batch))

    def verify_batch(
        self,
        batch: CdcBatch,
        *,
        checksum_sha256: str,
        expected_uri: str | None = None,
    ) -> StoredObject:
        stored = self.stat_batch(batch)
        if stored is None:
            raise RuntimeError("Manifest references a missing CDC Bronze object")
        if expected_uri is not None and stored.uri != expected_uri:
            raise RuntimeError("CDC Bronze object URI differs from manifest evidence")
        self._verify(stored, checksum_sha256)
        return stored

    @staticmethod
    def _verify(
        stored: StoredObject,
        checksum_sha256: str,
        expected_size: int | None = None,
    ) -> None:
        if stored.checksum_sha256 != checksum_sha256:
            raise RuntimeError("CDC Bronze object failed checksum verification")
        if expected_size is not None and stored.size_bytes != expected_size:
            raise RuntimeError("CDC Bronze object failed size verification")


def create_cdc_storage(settings: StorageSettings) -> CdcObjectStorage:
    """Create the selected backend without exposing MinIO SDK to consumer logic."""

    return CdcObjectStorage(
        create_storage_backend(settings),
        bronze_bucket=settings.bronze_bucket,
        quarantine_bucket=settings.quarantine_bucket,
    )
