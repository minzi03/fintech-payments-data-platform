"""Confidential immutable MinIO/local quarantine for poison Kafka records."""

from __future__ import annotations

from dataclasses import dataclass

from common.storage import StoredObject
from ingestion.cdc_consumer.models import PoisonEvent, deterministic_json
from ingestion.cdc_consumer.retry import RetryPolicy, retry_call
from ingestion.cdc_consumer.storage import CdcObjectStorage, build_dlq_object_key


@dataclass(frozen=True, slots=True)
class DlqResult:
    object_uri: str
    checksum_sha256: str
    already_exists: bool


class MinioQuarantineDlq:
    """Write poison evidence durably before its source Kafka offset is committed."""

    def __init__(
        self,
        storage: CdcObjectStorage,
        *,
        dlq_name: str,
        consumer_group: str,
        retry_policy: RetryPolicy,
    ) -> None:
        self._storage = storage
        self._dlq_name = dlq_name
        self._consumer_group = consumer_group
        self._retry_policy = retry_policy

    def write(self, event: PoisonEvent) -> DlqResult:
        key = build_dlq_object_key(
            dlq_name=self._dlq_name,
            consumer_group=self._consumer_group,
            topic=event.original_topic,
            partition=event.original_partition,
            offset=event.original_offset,
            event_id=event.event_id,
        )
        data = (deterministic_json(event.to_dict(self._consumer_group)) + "\n").encode("utf-8")

        def upload() -> StoredObject:
            return self._storage.backend.put_bytes_immutable(
                bucket=self._storage.quarantine_bucket,
                object_key=key,
                data=data,
                content_type="application/json",
                metadata={
                    "source_name": "kafka-cdc-consumer",
                    "artifact_type": "cdc_poison_record",
                    "consumer_group": self._consumer_group,
                    "dlq_topic": self._dlq_name,
                    "topic": event.original_topic,
                    "partition": event.original_partition,
                    "kafka_offset": event.original_offset,
                    "error_code": event.error_code,
                },
            )

        stored = retry_call(upload, policy=self._retry_policy)
        return DlqResult(stored.uri, stored.checksum_sha256, stored.already_exists)
