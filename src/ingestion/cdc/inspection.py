"""Bounded, offset-free Kafka console inspection for Phase 4 diagnostics."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .envelope import EnvelopeError, EnvelopeSummary, key_payload, parse_envelope


class TopicInspectionError(RuntimeError):
    """Raised when a bounded Kafka diagnostic cannot return records."""


@dataclass(frozen=True, slots=True)
class InspectedRecord:
    """Kafka position plus redacted Debezium metadata."""

    topic: str
    partition: int | None
    offset: int | None
    key: dict[str, object] | None
    envelope: EnvelopeSummary | None
    tombstone: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "partition": self.partition,
            "offset": self.offset,
            "key": self.key,
            "tombstone": self.tombstone,
            "op": self.envelope.op if self.envelope else None,
            "table": self.envelope.table if self.envelope else None,
            "snapshot": self.envelope.snapshot if self.envelope else None,
            "source_lsn": self.envelope.source_lsn if self.envelope else None,
            "source_ts_ms": self.envelope.source_ts_ms if self.envelope else None,
            "event_ts_ms": self.envelope.event_ts_ms if self.envelope else None,
            "transaction_id": self.envelope.transaction_id if self.envelope else None,
        }


def build_console_consumer_command(
    *,
    topic: str,
    max_messages: int,
    timeout_ms: int,
    compose_env: str,
) -> list[str]:
    """Build a diagnostic consumer command without a durable consumer group."""
    if max_messages < 1 or max_messages > 10_000:
        raise ValueError("max_messages must be between 1 and 10000")
    if timeout_ms < 1_000 or timeout_ms > 300_000:
        raise ValueError("timeout_ms must be between 1000 and 300000")
    return [
        "docker",
        "compose",
        "--env-file",
        compose_env,
        "exec",
        "-T",
        "kafka",
        "/opt/kafka/bin/kafka-console-consumer.sh",
        "--bootstrap-server",
        "kafka:9092",
        "--topic",
        topic,
        "--from-beginning",
        "--max-messages",
        str(max_messages),
        "--timeout-ms",
        str(timeout_ms),
        "--formatter-property",
        "print.partition=true",
        "--formatter-property",
        "print.offset=true",
        "--formatter-property",
        "print.key=true",
        "--formatter-property",
        "key.separator=\t",
    ]


def consume_topic(
    *,
    topic: str,
    max_messages: int = 100,
    timeout_ms: int = 10_000,
    compose_env: str = ".env.example",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[InspectedRecord, ...]:
    """Read from the beginning without committing offsets and return metadata-only records."""
    command = build_console_consumer_command(
        topic=topic,
        max_messages=max_messages,
        timeout_ms=timeout_ms,
        compose_env=compose_env,
    )
    result = runner(command, capture_output=True, text=True, check=False)
    records = tuple(
        record
        for line in result.stdout.splitlines()
        if (record := parse_console_record(line, topic=topic)) is not None
    )
    if result.returncode not in {0, 1} or (not records and result.returncode != 0):
        raise TopicInspectionError("Kafka topic inspection failed or timed out without records")
    return records


def parse_console_record(line: str, *, topic: str) -> InspectedRecord | None:
    """Parse DefaultMessageFormatter output with partition, offset, key, and value."""
    if not line.strip():
        return None
    parts = line.split("\t")
    partition = _prefixed_int(parts, "Partition:")
    offset = _prefixed_int(parts, "Offset:")
    json_parts = [part for part in parts if part.startswith("{") or part == "null"]
    if len(json_parts) < 2:
        return None
    key_document = _json_mapping(json_parts[-2])
    value_text = json_parts[-1]
    if value_text == "null":
        return InspectedRecord(
            topic=topic,
            partition=partition,
            offset=offset,
            key=key_payload(key_document),
            envelope=None,
            tombstone=True,
        )
    value_document = _json_mapping(value_text)
    if value_document is None:
        return None
    try:
        envelope = parse_envelope(value_document)
    except EnvelopeError:
        return None
    return InspectedRecord(
        topic=topic,
        partition=partition,
        offset=offset,
        key=key_payload(key_document),
        envelope=envelope,
        tombstone=False,
    )


def _prefixed_int(parts: Sequence[str], prefix: str) -> int | None:
    for part in parts:
        if part.startswith(prefix):
            try:
                return int(part.removeprefix(prefix))
            except ValueError:
                return None
    return None


def _json_mapping(value: str) -> dict[str, Any] | None:
    if value == "null":
        return None
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None
