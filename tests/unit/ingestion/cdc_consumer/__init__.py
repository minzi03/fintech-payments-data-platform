"""Shared deterministic fixtures for CDC consumer unit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ingestion.cdc_consumer.models import BronzeEvent, RawKafkaMessage, deterministic_event_id

TOPIC = "fintech.cdc.payments.payment_transactions"


def make_event(
    offset: int,
    *,
    topic: str = TOPIC,
    partition: int = 0,
    entity: str = "payment_transactions",
    operation: str = "c",
    event_date: str = "2026-07-22",
) -> BronzeEvent:
    return BronzeEvent(
        event_id=deterministic_event_id(topic, partition, offset),
        entity_name=entity,
        operation=operation,
        is_snapshot=operation == "r",
        is_deleted=operation == "d",
        is_tombstone=operation == "t",
        event_key_json='{"transaction_id":"tx-001"}',
        before_json='{"amount":"123.45"}' if operation in {"u", "d"} else None,
        after_json='{"amount":"123.45"}' if operation in {"r", "c", "u"} else None,
        source_metadata_json='{"lsn":100,"table":"payment_transactions"}',
        source_lsn=100 + offset,
        source_tx_id=42,
        source_ts_ms=1_753_158_600_000,
        connector_ts_ms=1_753_158_600_100,
        kafka_topic=topic,
        kafka_partition=partition,
        kafka_offset=offset,
        kafka_message_ts_ms=1_753_158_600_200,
        ingested_at=datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
        event_date=event_date,
        schema_version="cdc-bronze-v1",
        raw_event_json='{"payload":{"op":"c"}}',
    )


def make_message(
    offset: int,
    *,
    operation: str = "c",
    wrapper: bool = True,
    value: bytes | object | None = ...,  # type: ignore[assignment]
    partition: int = 0,
) -> RawKafkaMessage:
    if value is ...:
        before = {"transaction_id": "tx-001", "amount": "123.45"}
        after = {"transaction_id": "tx-001", "amount": "123.45"}
        payload = {
            "before": before if operation in {"u", "d"} else None,
            "after": after if operation in {"r", "c", "u"} else None,
            "op": operation,
            "source": {
                "table": "payment_transactions",
                "lsn": 100 + offset,
                "txId": 42,
                "ts_ms": 1_753_158_600_000,
            },
            "ts_ms": 1_753_158_600_100,
        }
        document = {"schema": {"type": "struct"}, "payload": payload} if wrapper else payload
        raw_value: bytes | None = json.dumps(document, separators=(",", ":")).encode()
    else:
        raw_value = value if isinstance(value, bytes) or value is None else bytes(value)
    return RawKafkaMessage(
        topic=TOPIC,
        partition=partition,
        offset=offset,
        key=b'{"payload":{"transaction_id":"tx-001"}}',
        value=raw_value,
        timestamp_ms=1_753_158_600_200,
    )
