"""Kafka -> Parquet -> MinIO commit/replay acceptance tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from confluent_kafka import ConsumerGroupTopicPartitions, TopicPartition
from confluent_kafka.admin import AdminClient

from common.storage import sha256_bytes
from ingestion.cdc_consumer.manifest import SqliteBatchManifest
from ingestion.cdc_consumer.models import BatchStatus, build_batch, deterministic_event_id
from ingestion.cdc_consumer.recovery import CdcBatchProcessor
from ingestion.cdc_consumer.retry import RetryExhaustedError, RetryPolicy
from ingestion.cdc_consumer.storage import build_dlq_object_key
from tests.unit.ingestion.cdc_consumer import make_event

from .conftest import ConsumerEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.cdc_consumer_integration]


def _object_bytes(environment: ConsumerEnvironment, uri: str) -> bytes:
    prefix = f"s3://{environment.storage.bronze_bucket}/"
    assert uri.startswith(prefix)
    return environment.storage.backend.read_bytes(
        environment.storage.bronze_bucket, uri.removeprefix(prefix)
    )


def test_real_consumer_writes_verified_explicit_parquet(
    consumer_environment: ConsumerEnvironment,
) -> None:
    records = consumer_environment.manifest.list_all()
    assert records
    assert all(record.status is BatchStatus.COMMITTED for record in records)
    operations: set[str] = set()
    partitions: set[int] = set()
    for record in records:
        assert record.object_uri and record.checksum_sha256
        payload = _object_bytes(consumer_environment, record.object_uri)
        assert sha256_bytes(payload) == record.checksum_sha256
        table = pq.read_table(pa.BufferReader(payload))
        assert table.schema.field("kafka_offset").type == pa.int64()
        assert table.schema.field("ingested_at").type.tz == "UTC"
        operations.update(str(value) for value in table["operation"].to_pylist())
        partitions.update(int(value) for value in table["kafka_partition"].to_pylist())
        assert any(value is not None for value in table["source_lsn"].to_pylist()) or "t" in set(
            table["operation"].to_pylist()
        )
    assert {"r", "c", "u", "d", "t"} <= operations
    assert {0, 1} <= partitions
    assert consumer_environment.first_run.poison_count >= 1


def test_kafka_offsets_are_committed_as_manifest_end_plus_one(
    consumer_environment: ConsumerEnvironment,
) -> None:
    records = consumer_environment.manifest.list_all()
    highest: dict[tuple[str, int], int] = {}
    for record in records:
        key = (record.topic, record.partition)
        highest[key] = max(highest.get(key, -1), record.offset_end + 1)
    admin = AdminClient({"bootstrap.servers": consumer_environment.settings.bootstrap_servers})
    request = ConsumerGroupTopicPartitions(
        consumer_environment.settings.group_id,
        [TopicPartition(topic, partition) for topic, partition in highest],
    )
    future = admin.list_consumer_group_offsets([request], request_timeout=30)[
        consumer_environment.settings.group_id
    ]
    committed = future.result(timeout=35).topic_partitions
    actual = {(item.topic, item.partition): item.offset for item in committed}
    assert all(actual[key] >= next_offset for key, next_offset in highest.items())


def test_restart_same_group_does_not_reprocess_committed_ranges(
    consumer_environment: ConsumerEnvironment,
) -> None:
    assert consumer_environment.second_run.event_count == 0
    assert consumer_environment.second_run.batch_count == 0


def test_poison_record_is_immutable_quarantine_before_commit(
    consumer_environment: ConsumerEnvironment,
) -> None:
    event_id = deterministic_event_id(
        consumer_environment.poison_topic,
        consumer_environment.poison_partition,
        consumer_environment.poison_offset,
    )
    key = build_dlq_object_key(
        dlq_name=consumer_environment.settings.dlq_topic,
        consumer_group=consumer_environment.settings.group_id,
        topic=consumer_environment.poison_topic,
        partition=consumer_environment.poison_partition,
        offset=consumer_environment.poison_offset,
        event_id=event_id,
    )
    stored = consumer_environment.storage.backend.stat(
        consumer_environment.storage.quarantine_bucket, key
    )
    assert consumer_environment.first_run.poison_count >= 1
    assert stored is not None
    payload = consumer_environment.storage.backend.read_bytes(
        consumer_environment.storage.quarantine_bucket, key
    )
    assert b"MALFORMED_JSON" in payload
    assert b"malformed-private-json" not in payload


@dataclass
class _Committer:
    fail: bool
    offsets: list[int] = field(default_factory=list)

    def commit(self, *, topic: str, partition: int, next_offset: int) -> None:
        del topic, partition
        if self.fail:
            raise ConnectionError("simulated crash window")
        self.offsets.append(next_offset)


def test_real_minio_replay_after_upload_before_commit_is_idempotent(
    tmp_path, consumer_environment: ConsumerEnvironment
) -> None:
    partition = 2
    offset = 9_000_000 + (hash(consumer_environment.synthetic_marker) % 100_000)
    batch = build_batch((make_event(offset, partition=partition),))
    manifest = SqliteBatchManifest(tmp_path / "crash-manifest.sqlite3")
    retry = RetryPolicy(max_attempts=1, initial_backoff_seconds=0.01)
    failed_committer = _Committer(fail=True)
    first = CdcBatchProcessor(
        manifest=manifest,
        storage=consumer_environment.storage,
        committer=failed_committer,
        consumer_group=consumer_environment.settings.group_id,
        temp_dir=tmp_path / "temp",
        retry_policy=retry,
    )
    with pytest.raises(RetryExhaustedError):
        first.process(batch)
    uploaded = manifest.get(batch.batch_id)
    assert uploaded is not None and uploaded.status is BatchStatus.UPLOADED

    healthy_committer = _Committer(fail=False)
    second = CdcBatchProcessor(
        manifest=manifest,
        storage=consumer_environment.storage,
        committer=healthy_committer,
        consumer_group=consumer_environment.settings.group_id,
        temp_dir=tmp_path / "temp",
        retry_policy=retry,
    )
    result = second.process(batch)
    assert result.replayed
    assert healthy_committer.offsets == [batch.offset_end + 1]
    assert manifest.get(batch.batch_id).status is BatchStatus.COMMITTED  # type: ignore[union-attr]
