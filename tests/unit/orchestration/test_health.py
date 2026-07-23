from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from confluent_kafka import TopicPartition

from orchestration import health


class FakeConsumer:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.closed = False

    def list_topics(self, timeout: int):
        assert timeout == 10
        return SimpleNamespace(
            topics={"topic-a": SimpleNamespace(partitions={0: object(), 1: object()})}
        )

    def committed(self, partitions, timeout: int):
        assert timeout == 10
        return [TopicPartition(item.topic, item.partition, 3) for item in partitions]

    def get_watermark_offsets(self, partition, timeout: int, cached: bool):
        assert timeout == 10 and cached is False
        return (0, 10 + partition.partition)

    def close(self) -> None:
        self.closed = True


def test_kafka_health_and_partition_lag(monkeypatch) -> None:
    monkeypatch.setattr(health, "Consumer", FakeConsumer)

    healthy = health.check_kafka("kafka:9092", ("topic-a",))
    lag = health.consumer_group_lag("kafka:9092", "group-a", ("topic-a",))

    assert healthy == {"healthy": True, "topic_count": 1}
    assert lag["total_lag"] == 15
    assert len(lag["partitions"]) == 2


def test_kafka_health_rejects_missing_topics(monkeypatch) -> None:
    monkeypatch.setattr(health, "Consumer", FakeConsumer)

    with pytest.raises(RuntimeError, match="missing"):
        health.check_kafka("kafka:9092", ("missing-topic",))


def test_connector_status_is_bounded_and_requires_running_tasks(monkeypatch) -> None:
    class FakePool:
        def request(self, method: str, url: str):
            assert method == "GET"
            assert url.endswith("/connectors/payments/status")
            return SimpleNamespace(
                status=200,
                data=json.dumps(
                    {"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]}
                ).encode(),
            )

    monkeypatch.setattr(health.urllib3, "PoolManager", lambda **kwargs: FakePool())

    result = health.check_connector("http://connect:8083", "payments")

    assert result == {"healthy": True, "connector_state": "RUNNING", "task_count": 1}


def test_postgres_logical_replication_check(monkeypatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query: str):
            assert "wal_level" in query
            return SimpleNamespace(fetchone=lambda: ("logical", "10"))

    monkeypatch.setattr(health.psycopg, "connect", lambda uri: FakeConnection())

    assert health.check_postgres_logical_replication("postgresql://safe") == {
        "healthy": True,
        "wal_level": "logical",
        "max_replication_slots": 10,
    }


def test_manifest_freshness_uses_committed_batches(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite3"
    committed_at = datetime(2026, 7, 23, tzinfo=UTC)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE cdc_batch_manifest (status TEXT NOT NULL, committed_at TEXT)"
        )
        connection.execute(
            "INSERT INTO cdc_batch_manifest VALUES ('COMMITTED', ?)",
            (committed_at.isoformat(),),
        )

    result = health.cdc_manifest_freshness(path, now=committed_at + timedelta(seconds=90))

    assert result["freshness_seconds"] == 90
    assert result["committed_batch_count"] == 1
