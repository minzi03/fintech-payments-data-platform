"""Bounded loop, DLQ-before-commit, rebalance, and shutdown tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import TopicPartition

from ingestion.cdc_consumer.config import CdcConsumerSettings
from ingestion.cdc_consumer.consumer import ReliableCdcConsumer
from tests.unit.ingestion.cdc_consumer import make_message


class FakeKafkaMessage:
    def __init__(self, raw) -> None:
        self.raw = raw

    def topic(self):
        return self.raw.topic

    def partition(self):
        return self.raw.partition

    def offset(self):
        return self.raw.offset

    def key(self):
        return self.raw.key

    def value(self):
        return self.raw.value

    def timestamp(self):
        return (1, self.raw.timestamp_ms)

    def error(self):
        return None


class Revoke:
    pass


class FakeConsumer:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = list(messages)
        self.commits: list[tuple[str, int, int]] = []
        self.closed = False
        self.callbacks: dict[str, Any] = {}

    def subscribe(self, _topics, **callbacks):
        self.callbacks = callbacks
        callbacks["on_assign"](
            self, [TopicPartition("fintech.cdc.payments.payment_transactions", 0)]
        )

    def poll(self, _timeout):
        if not self.messages:
            return None
        item = self.messages.pop(0)
        if isinstance(item, Revoke):
            self.callbacks["on_revoke"](
                self, [TopicPartition("fintech.cdc.payments.payment_transactions", 0)]
            )
            return None
        return FakeKafkaMessage(item)

    def commit(self, *, offsets, asynchronous):
        assert asynchronous is False
        self.commits.extend((item.topic, item.partition, item.offset) for item in offsets)
        return offsets

    def committed(self, partitions, timeout):
        del timeout
        return [TopicPartition(item.topic, item.partition, -1001) for item in partitions]

    def close(self):
        self.closed = True


@dataclass
class FakeProcessor:
    batches: list[Any] = field(default_factory=list)
    reconciled: list[dict[tuple[str, int], int]] = field(default_factory=list)

    def process(self, batch):
        self.batches.append(batch)

    def reconcile_committed_offsets(self, offsets):
        self.reconciled.append(offsets)
        return 0


@dataclass
class FakeDlq:
    events: list[Any] = field(default_factory=list)

    def write(self, event):
        self.events.append(event)


def settings(batch_size: int = 10) -> CdcConsumerSettings:
    return CdcConsumerSettings.from_env(
        {
            "KAFKA_BOOTSTRAP_SERVERS": "localhost:29092",
            "KAFKA_TOPIC_PREFIX": "fintech.cdc",
            "CDC_CONSUMER_TOPICS": "fintech.cdc.payments.payment_transactions",
            "CDC_CONSUMER_BATCH_SIZE": str(batch_size),
            "CDC_CONSUMER_POLL_TIMEOUT_MS": "10",
        }
    )


def test_clean_once_flushes_pending_batch_and_closes() -> None:
    client = FakeConsumer([make_message(0), make_message(1)])
    processor = FakeProcessor()
    service = ReliableCdcConsumer(
        settings=settings(),
        processor=processor,  # type: ignore[arg-type]
        dlq=FakeDlq(),  # type: ignore[arg-type]
        consumer=client,
    )
    result = service.run(once=True, install_signal_handlers=False)
    assert result.event_count == 2
    assert len(processor.batches) == 1
    assert processor.batches[0].offset_end == 1
    assert client.closed


def test_poison_is_written_to_dlq_before_source_commit(caplog) -> None:
    raw = make_message(9, value=b"private-not-json")
    client = FakeConsumer([raw])
    dlq = FakeDlq()
    service = ReliableCdcConsumer(
        settings=settings(),
        processor=FakeProcessor(),  # type: ignore[arg-type]
        dlq=dlq,  # type: ignore[arg-type]
        consumer=client,
    )
    result = service.run(once=True, install_signal_handlers=False)
    assert result.poison_count == 1
    assert len(dlq.events) == 1
    assert client.commits == [(raw.topic, raw.partition, 10)]
    assert "private-not-json" not in caplog.text


def test_revoke_flushes_only_owned_pending_partition() -> None:
    client = FakeConsumer([make_message(3), Revoke()])
    processor = FakeProcessor()
    service = ReliableCdcConsumer(
        settings=settings(),
        processor=processor,  # type: ignore[arg-type]
        dlq=FakeDlq(),  # type: ignore[arg-type]
        consumer=client,
    )
    service.run(once=True, install_signal_handlers=False)
    assert len(processor.batches) == 1
    assert processor.batches[0].offset_start == 3


def test_dry_run_neither_uploads_dlq_nor_commits() -> None:
    client = FakeConsumer([make_message(0), make_message(1, value=b"broken")])
    processor = FakeProcessor()
    dlq = FakeDlq()
    service = ReliableCdcConsumer(
        settings=settings(),
        processor=processor,  # type: ignore[arg-type]
        dlq=dlq,  # type: ignore[arg-type]
        consumer=client,
        dry_run=True,
    )
    result = service.run(once=True, install_signal_handlers=False)
    assert result.dry_run
    assert processor.batches == []
    assert dlq.events == []
    assert client.commits == []
