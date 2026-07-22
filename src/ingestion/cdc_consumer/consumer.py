"""Confluent Kafka consumer with partition-scoped durable offset commits."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from ingestion.cdc_consumer.batching import PartitionBatcher
from ingestion.cdc_consumer.config import CdcConsumerSettings
from ingestion.cdc_consumer.dlq import MinioQuarantineDlq
from ingestion.cdc_consumer.envelope import parse_debezium_message
from ingestion.cdc_consumer.models import (
    CdcBatch,
    ConsumerRunResult,
    ParseKind,
    RawKafkaMessage,
    TopicPartitionKey,
)
from ingestion.cdc_consumer.recovery import CdcBatchProcessor, OffsetCommitter

LOGGER = logging.getLogger(__name__)


class ConsumerClient(Protocol):
    def subscribe(self, topics: list[str], **callbacks: Any) -> None: ...

    def poll(self, timeout: float) -> Any: ...

    def commit(
        self, *, offsets: list[TopicPartition], asynchronous: bool
    ) -> list[TopicPartition] | None: ...

    def committed(
        self, partitions: list[TopicPartition], timeout: float
    ) -> list[TopicPartition]: ...

    def close(self) -> None: ...


class KafkaOffsetCommitter(OffsetCommitter):
    def __init__(self, consumer: ConsumerClient) -> None:
        self._consumer = consumer

    def commit(self, *, topic: str, partition: int, next_offset: int) -> None:
        try:
            result = self._consumer.commit(
                offsets=[TopicPartition(topic, partition, next_offset)],
                asynchronous=False,
            )
        except KafkaException as exc:
            raise ConnectionError("Synchronous Kafka offset commit failed") from exc
        for committed in result or []:
            error = committed.error
            if error is not None:
                raise ConnectionError("Kafka rejected a partition offset commit")


@dataclass
class _Counters:
    polled: int = 0
    events: int = 0
    poison: int = 0
    heartbeats: int = 0
    batches: int = 0
    committed: int = 0


class ReliableCdcConsumer:
    """Run a bounded or long-lived CDC loop without automatic offset storage."""

    def __init__(
        self,
        *,
        settings: CdcConsumerSettings,
        processor: CdcBatchProcessor,
        dlq: MinioQuarantineDlq,
        consumer: ConsumerClient | None = None,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._consumer = consumer or Consumer(settings.kafka_config())
        self._processor = processor
        self._dlq = dlq
        self._dry_run = dry_run
        self._batcher = PartitionBatcher(
            batch_size=settings.batch_size,
            flush_interval_seconds=settings.flush_interval_seconds,
        )
        self._stop = threading.Event()
        self._counters = _Counters()
        self._fatal = False
        self._assignment_seen = False

    @property
    def offset_committer(self) -> KafkaOffsetCommitter:
        return KafkaOffsetCommitter(self._consumer)

    def request_stop(self) -> None:
        self._stop.set()

    def run(
        self,
        *,
        once: bool = False,
        max_messages: int | None = None,
        install_signal_handlers: bool = True,
    ) -> ConsumerRunResult:
        if max_messages is not None and max_messages <= 0:
            raise ValueError("max_messages must be greater than zero")
        if install_signal_handlers and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_stop)
            signal.signal(signal.SIGTERM, self._signal_stop)

        self._consumer.subscribe(
            list(self._settings.topics),
            on_assign=self._on_assign,
            on_revoke=self._on_revoke,
        )
        clean_shutdown = False
        empty_polls = 0
        once_assignment_deadline = time.monotonic() + min(
            30.0,
            max(10.0, self._settings.session_timeout_ms / 1_000),
        )
        try:
            while not self._stop.is_set():
                message = self._consumer.poll(self._settings.poll_timeout_ms / 1_000)
                if message is None:
                    self._flush_batches(self._batcher.flush_due())
                    if once:
                        if not self._assignment_seen:
                            if time.monotonic() >= once_assignment_deadline:
                                raise TimeoutError(
                                    "Kafka assignment was not received during bounded run"
                                )
                            continue
                        empty_polls += 1
                        if empty_polls >= 2:
                            clean_shutdown = True
                            break
                    continue
                empty_polls = 0
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(error)

                raw = _raw_message(message)
                self._counters.polled += 1
                parsed = parse_debezium_message(
                    raw,
                    schema_version=self._settings.schema_version,
                )
                if parsed.kind is ParseKind.EVENT:
                    assert parsed.event is not None
                    self._counters.events += 1
                    self._flush_batches(self._batcher.add(parsed.event))
                elif parsed.kind is ParseKind.POISON:
                    assert parsed.poison is not None
                    self._counters.poison += 1
                    self._flush_batches(self._batcher.flush_partition(parsed.raw.topic_partition))
                    if not self._dry_run:
                        self._dlq.write(parsed.poison)
                        self.offset_committer.commit(
                            topic=raw.topic,
                            partition=raw.partition,
                            next_offset=raw.offset + 1,
                        )
                        self._counters.committed += 1
                else:
                    self._counters.heartbeats += 1
                    if not self._dry_run:
                        self.offset_committer.commit(
                            topic=raw.topic,
                            partition=raw.partition,
                            next_offset=raw.offset + 1,
                        )
                        self._counters.committed += 1

                if max_messages is not None and self._counters.polled >= max_messages:
                    clean_shutdown = True
                    break
            else:
                clean_shutdown = True

            if self._stop.is_set():
                clean_shutdown = True
            if clean_shutdown:
                self._flush_batches(self._batcher.flush_all())
        except Exception:
            self._fatal = True
            raise
        finally:
            # Never flush after a fatal poison/upload/commit failure because doing
            # so could advance a partition beyond an unresolved source record.
            self._consumer.close()

        return ConsumerRunResult(
            polled_count=self._counters.polled,
            event_count=self._counters.events,
            poison_count=self._counters.poison,
            heartbeat_count=self._counters.heartbeats,
            batch_count=self._counters.batches,
            committed_count=self._counters.committed,
            dry_run=self._dry_run,
        )

    def _flush_batches(self, batches: Sequence[CdcBatch]) -> None:
        for batch in batches:
            self._counters.batches += 1
            if self._dry_run:
                continue
            self._processor.process(batch)
            self._counters.committed += 1

    def _on_revoke(self, _consumer: ConsumerClient, partitions: list[TopicPartition]) -> None:
        keys = [TopicPartitionKey(item.topic, item.partition) for item in partitions]
        self._flush_batches(self._batcher.flush_partitions(keys))

    def _on_assign(self, consumer: ConsumerClient, partitions: list[TopicPartition]) -> None:
        self._assignment_seen = True
        if self._dry_run or not partitions:
            return
        try:
            committed = consumer.committed(partitions, timeout=5.0)
        except KafkaException as exc:
            raise ConnectionError("Could not inspect committed Kafka offsets") from exc
        offsets = {
            (item.topic, item.partition): item.offset
            for item in committed
            if item.offset is not None and item.offset >= 0
        }
        self._processor.reconcile_committed_offsets(offsets)

    def _signal_stop(self, signum: int, _frame: object) -> None:
        LOGGER.info("Received shutdown signal", extra={"signal": signum})
        self.request_stop()


def _raw_message(message: Any) -> RawKafkaMessage:
    timestamp_type, timestamp_ms = message.timestamp()
    del timestamp_type
    return RawKafkaMessage(
        topic=str(message.topic()),
        partition=int(message.partition()),
        offset=int(message.offset()),
        key=message.key(),
        value=message.value(),
        timestamp_ms=timestamp_ms if timestamp_ms is not None and timestamp_ms >= 0 else None,
    )
