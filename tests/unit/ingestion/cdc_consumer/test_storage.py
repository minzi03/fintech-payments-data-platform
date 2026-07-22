"""CDC object layout, metadata, and immutable collision tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.storage import ImmutableCollisionError, LocalStorageBackend, sha256_bytes
from ingestion.cdc_consumer.dlq import MinioQuarantineDlq
from ingestion.cdc_consumer.models import PoisonEvent, build_batch, deterministic_event_id
from ingestion.cdc_consumer.parquet import (
    SerializedParquet,
    cleanup_serialized,
    serialize_batch,
)
from ingestion.cdc_consumer.retry import RetryPolicy
from ingestion.cdc_consumer.storage import (
    CdcObjectStorage,
    build_cdc_object_key,
    build_dlq_object_key,
)
from tests.unit.ingestion.cdc_consumer import TOPIC, make_event


def storage(tmp_path) -> CdcObjectStorage:
    return CdcObjectStorage(
        LocalStorageBackend(
            {
                "fintech-bronze": tmp_path / "bronze",
                "fintech-quarantine": tmp_path / "quarantine",
            }
        ),
        bronze_bucket="fintech-bronze",
        quarantine_bucket="fintech-quarantine",
    )


def test_object_key_is_deterministic_and_partition_aware() -> None:
    batch = build_batch((make_event(100, partition=2), make_event(101, partition=2)))
    key = build_cdc_object_key(batch)
    assert "entity=payment_transactions" in key
    assert f"topic={TOPIC}/partition=2" in key
    assert "offset_start=100/offset_end=101" in key
    assert key.endswith(f"batch_id={batch.batch_id}.parquet")


def test_local_backend_idempotency_metadata_and_collision(tmp_path) -> None:
    target = storage(tmp_path)
    batch = build_batch((make_event(3),))
    timestamp = datetime(2026, 7, 22, tzinfo=UTC)
    serialized = serialize_batch(batch, temp_dir=tmp_path / "temp", ingested_at=timestamp)
    try:
        first = target.put_batch(
            batch, serialized, consumer_group="consumer-a", ingested_at=timestamp
        )
        second = target.put_batch(
            batch, serialized, consumer_group="consumer-a", ingested_at=timestamp
        )
        assert second.already_exists
        assert first.checksum_sha256 == second.checksum_sha256
        assert first.metadata["consumer_group"] == "consumer-a"
        assert first.metadata["offset_start"] == "3"

        different = b"different content"
        serialized.path.write_bytes(different)
        collision = SerializedParquet(
            path=serialized.path,
            checksum_sha256=sha256_bytes(different),
            size_bytes=len(different),
        )
        with pytest.raises(ImmutableCollisionError):
            target.put_batch(batch, collision, consumer_group="consumer-a", ingested_at=timestamp)
    finally:
        cleanup_serialized(serialized)


def test_dlq_key_contains_only_coordinates_and_identity() -> None:
    key = build_dlq_object_key(
        dlq_name="fintech.cdc.dlq",
        consumer_group="consumer-a",
        topic=TOPIC,
        partition=1,
        offset=9,
        event_id="e" * 64,
    )
    assert "offset=9" in key
    assert "transaction_id" not in key


def test_dlq_write_is_confidential_and_idempotent(tmp_path) -> None:
    target = storage(tmp_path)
    dlq = MinioQuarantineDlq(
        target,
        dlq_name="fintech.cdc.dlq",
        consumer_group="consumer-a",
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001),
    )
    event = PoisonEvent(
        event_id=deterministic_event_id(TOPIC, 0, 99),
        error_code="MALFORMED_JSON",
        error_message="Value is not valid JSON",
        original_topic=TOPIC,
        original_partition=0,
        original_offset=99,
        original_key="cHJpdmF0ZS1rZXk=",
        raw_value="cHJpdmF0ZS12YWx1ZQ==",
        failed_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    first = dlq.write(event)
    second = dlq.write(event)
    assert first.checksum_sha256 == second.checksum_sha256
    assert second.already_exists
    key = build_dlq_object_key(
        dlq_name="fintech.cdc.dlq",
        consumer_group="consumer-a",
        topic=TOPIC,
        partition=0,
        offset=99,
        event_id=event.event_id,
    )
    payload = target.backend.read_bytes(target.quarantine_bucket, key)
    assert b"private-value" not in payload
    assert b"cHJpdmF0ZS12YWx1ZQ==" in payload
