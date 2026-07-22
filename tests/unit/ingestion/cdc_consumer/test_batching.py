"""Partition-aware bounded micro-batch tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ingestion.cdc_consumer.batching import PartitionBatcher
from ingestion.cdc_consumer.models import TopicPartitionKey, deterministic_batch_id
from tests.unit.ingestion.cdc_consumer import TOPIC, make_event


def test_batch_id_and_event_ids_are_deterministic() -> None:
    expected = deterministic_batch_id(TOPIC, 0, 10, 11, "cdc-bronze-v1")
    batcher = PartitionBatcher(batch_size=2, flush_interval_seconds=5)
    assert batcher.add(make_event(10)) == []
    batches = batcher.add(make_event(11))
    assert batches[0].batch_id == expected
    assert batches[0].next_offset == 12


def test_partitions_are_never_mixed() -> None:
    batcher = PartitionBatcher(batch_size=10, flush_interval_seconds=5)
    batcher.add(make_event(0, partition=0))
    batcher.add(make_event(0, partition=1))
    batches = batcher.flush_all()
    assert len(batches) == 2
    assert {batch.partition for batch in batches} == {0, 1}


def test_gap_flushes_previous_contiguous_range() -> None:
    batcher = PartitionBatcher(batch_size=10, flush_interval_seconds=5)
    batcher.add(make_event(2))
    batches = batcher.add(make_event(4))
    assert [(batch.offset_start, batch.offset_end) for batch in batches] == [(2, 2)]
    assert batcher.flush_all()[0].offset_start == 4


def test_size_and_time_trigger_bounded_flush() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    batcher = PartitionBatcher(batch_size=2, flush_interval_seconds=5)
    batcher.add(make_event(0), now=now)
    assert batcher.flush_due(now=now + timedelta(seconds=4)) == []
    timed = batcher.flush_due(now=now + timedelta(seconds=5))
    assert timed[0].record_count == 1

    batcher.add(make_event(1), now=now)
    sized = batcher.add(make_event(2), now=now)
    assert sized[0].record_count == 2


def test_revoke_flushes_only_selected_partition() -> None:
    batcher = PartitionBatcher(batch_size=10, flush_interval_seconds=5)
    batcher.add(make_event(0, partition=0))
    batcher.add(make_event(0, partition=1))
    revoked = batcher.flush_partition(TopicPartitionKey(TOPIC, 0))
    assert [batch.partition for batch in revoked] == [0]
    assert batcher.pending_count == 1
