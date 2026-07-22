"""Real Kafka/MinIO fixtures for Phase 5 consumer acceptance tests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from confluent_kafka import Consumer, Producer

from common.config import StorageSettings
from ingestion.cdc_consumer.config import CdcConsumerSettings
from ingestion.cdc_consumer.consumer import KafkaOffsetCommitter, ReliableCdcConsumer
from ingestion.cdc_consumer.dlq import MinioQuarantineDlq
from ingestion.cdc_consumer.manifest import SqliteBatchManifest
from ingestion.cdc_consumer.models import ConsumerRunResult
from ingestion.cdc_consumer.recovery import CdcBatchProcessor
from ingestion.cdc_consumer.retry import RetryPolicy
from ingestion.cdc_consumer.storage import CdcObjectStorage, create_cdc_storage


@dataclass(frozen=True, slots=True)
class ConsumerEnvironment:
    settings: CdcConsumerSettings
    storage: CdcObjectStorage
    manifest: SqliteBatchManifest
    first_run: ConsumerRunResult
    second_run: ConsumerRunResult
    synthetic_marker: str
    poison_topic: str
    poison_partition: int
    poison_offset: int


def _event(marker: str, operation: str, lsn: int) -> bytes:
    before = {"customer_id": marker, "status": "ACTIVE"}
    after = {"customer_id": marker, "status": "SUSPENDED"}
    payload = {
        "before": before if operation in {"u", "d"} else None,
        "after": after if operation in {"r", "c", "u"} else None,
        "op": operation,
        "source": {
            "connector": "postgresql",
            "db": "fintech_payments",
            "schema": "payments",
            "table": "customers",
            "lsn": lsn,
            "txId": 9001,
            "ts_ms": 1_753_158_600_000,
        },
        "ts_ms": 1_753_158_600_100,
    }
    return json.dumps({"schema": {"type": "struct"}, "payload": payload}).encode()


def _run_consumer(
    settings: CdcConsumerSettings,
    storage: CdcObjectStorage,
    manifest: SqliteBatchManifest,
) -> ConsumerRunResult:
    retry = RetryPolicy(max_attempts=3, initial_backoff_seconds=0.05)
    client = Consumer(settings.kafka_config())
    processor = CdcBatchProcessor(
        manifest=manifest,
        storage=storage,
        committer=KafkaOffsetCommitter(client),
        consumer_group=settings.group_id,
        temp_dir=settings.temp_dir,
        retry_policy=retry,
    )
    return ReliableCdcConsumer(
        settings=settings,
        processor=processor,
        dlq=MinioQuarantineDlq(
            storage,
            dlq_name=settings.dlq_topic,
            consumer_group=settings.group_id,
            retry_policy=retry,
        ),
        consumer=client,
    ).run(once=True, install_signal_handlers=False)


@pytest.fixture(scope="module")
def consumer_environment(tmp_path_factory) -> ConsumerEnvironment:
    if os.getenv("RUN_CDC_CONSUMER_INTEGRATION") != "1":
        pytest.skip("Set RUN_CDC_CONSUMER_INTEGRATION=1 and start Kafka plus MinIO")

    marker = f"phase5-{uuid4().hex}"
    topic = "fintech.cdc.payments.customers"
    base_settings = CdcConsumerSettings.from_env(os.environ)
    settings = base_settings.with_overrides(
        topics=[topic],
        group_id=f"phase5-it-{uuid4().hex}",
        batch_size=20,
        flush_interval_seconds=0.2,
    )
    runtime = Path(tmp_path_factory.mktemp("phase5-cdc-consumer"))
    settings = replace(
        settings,
        manifest_path=runtime / "manifest.sqlite3",
        temp_dir=runtime / "temp",
    )
    storage_settings = StorageSettings.from_env(os.environ, backend_override="minio")
    storage = create_cdc_storage(storage_settings)
    assert storage.backend.exists(storage.bronze_bucket, "health/missing") is False

    producer = Producer({"bootstrap.servers": settings.bootstrap_servers})
    for index, operation in enumerate(("r", "c", "u", "d")):
        producer.produce(
            topic,
            key=json.dumps({"customer_id": marker}).encode(),
            value=_event(marker, operation, 800_000 + index),
            partition=index % 2,
        )
    producer.produce(
        topic,
        key=json.dumps({"customer_id": marker}).encode(),
        value=None,
        partition=1,
    )
    poison_coordinates: list[tuple[str, int, int]] = []

    def record_delivery(error, message) -> None:
        assert error is None
        poison_coordinates.append((message.topic(), message.partition(), message.offset()))

    producer.produce(
        topic,
        key=b"confidential-key",
        value=b"malformed-private-json",
        partition=0,
        on_delivery=record_delivery,
    )
    undelivered = producer.flush(10)
    assert undelivered == 0

    manifest = SqliteBatchManifest(settings.manifest_path)
    first_run = _run_consumer(settings, storage, manifest)
    second_run = _run_consumer(settings, storage, manifest)
    assert len(poison_coordinates) == 1
    poison_topic, poison_partition, poison_offset = poison_coordinates[0]
    return ConsumerEnvironment(
        settings,
        storage,
        manifest,
        first_run,
        second_run,
        marker,
        poison_topic,
        poison_partition,
        poison_offset,
    )
