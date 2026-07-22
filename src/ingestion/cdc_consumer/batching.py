"""Partition-aware, bounded CDC micro-batching."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ingestion.cdc_consumer.models import (
    BronzeEvent,
    CdcBatch,
    TopicPartitionKey,
    build_batch,
)


@dataclass
class _PendingPartition:
    events: deque[BronzeEvent]
    first_seen_at: datetime


class PartitionBatcher:
    """Keep one bounded, contiguous event queue per Kafka partition."""

    def __init__(self, *, batch_size: int, flush_interval_seconds: float) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be greater than zero")
        self._batch_size = batch_size
        self._flush_interval = timedelta(seconds=flush_interval_seconds)
        self._pending: dict[TopicPartitionKey, _PendingPartition] = {}

    def add(self, event: BronzeEvent, *, now: datetime | None = None) -> list[CdcBatch]:
        observed_at = now or datetime.now(UTC)
        key = TopicPartitionKey(event.kafka_topic, event.kafka_partition)
        partition = self._pending.get(key)
        ready: list[CdcBatch] = []

        if partition is not None and partition.events:
            previous = partition.events[-1]
            boundary_changed = (
                event.kafka_offset != previous.kafka_offset + 1
                or event.entity_name != previous.entity_name
                or event.event_date != previous.event_date
                or event.schema_version != previous.schema_version
            )
            if boundary_changed:
                ready.extend(self.flush_partition(key))
                partition = None

        if partition is None:
            partition = _PendingPartition(events=deque(), first_seen_at=observed_at)
            self._pending[key] = partition

        partition.events.append(event)
        while len(partition.events) >= self._batch_size:
            ready.append(self._take(key, self._batch_size, observed_at))
            partition = self._pending.get(key)
            if partition is None:
                break
        return ready

    def flush_due(self, *, now: datetime | None = None) -> list[CdcBatch]:
        observed_at = now or datetime.now(UTC)
        due = [
            key
            for key, pending in self._pending.items()
            if pending.events and observed_at - pending.first_seen_at >= self._flush_interval
        ]
        batches: list[CdcBatch] = []
        for key in sorted(due, key=lambda item: (item.topic, item.partition)):
            batches.extend(self.flush_partition(key, now=observed_at))
        return batches

    def flush_partition(
        self,
        key: TopicPartitionKey,
        *,
        now: datetime | None = None,
    ) -> list[CdcBatch]:
        pending = self._pending.get(key)
        if pending is None or not pending.events:
            return []
        observed_at = now or datetime.now(UTC)
        return [self._take(key, len(pending.events), observed_at)]

    def flush_partitions(
        self,
        keys: Iterable[TopicPartitionKey],
        *,
        now: datetime | None = None,
    ) -> list[CdcBatch]:
        batches: list[CdcBatch] = []
        for key in keys:
            batches.extend(self.flush_partition(key, now=now))
        return batches

    def flush_all(self, *, now: datetime | None = None) -> list[CdcBatch]:
        return self.flush_partitions(list(self._pending), now=now)

    @property
    def pending_count(self) -> int:
        return sum(len(item.events) for item in self._pending.values())

    def _take(
        self,
        key: TopicPartitionKey,
        count: int,
        observed_at: datetime,
    ) -> CdcBatch:
        pending = self._pending[key]
        events = tuple(pending.events.popleft() for _ in range(count))
        if pending.events:
            pending.first_seen_at = observed_at
        else:
            del self._pending[key]
        return build_batch(events)
