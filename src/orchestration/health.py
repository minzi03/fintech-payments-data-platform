"""Bounded CDC dependency checks; this module never runs a streaming loop."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import urllib3
from confluent_kafka import Consumer, TopicPartition


def check_postgres_logical_replication(connection_uri: str) -> dict[str, object]:
    with psycopg.connect(connection_uri) as connection:
        row = connection.execute(
            "SELECT current_setting('wal_level'), current_setting('max_replication_slots')"
        ).fetchone()
    if row is None or row[0] != "logical":
        raise RuntimeError("PostgreSQL wal_level is not logical")
    return {"healthy": True, "wal_level": row[0], "max_replication_slots": int(row[1])}


def check_kafka(bootstrap_servers: str, topics: tuple[str, ...]) -> dict[str, object]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": "fintech-airflow-health-probe",
            "enable.auto.commit": False,
            "session.timeout.ms": 6000,
        }
    )
    try:
        metadata = consumer.list_topics(timeout=10)
        missing = sorted(set(topics) - set(metadata.topics))
        if missing:
            raise RuntimeError(f"Required CDC topics are missing: {', '.join(missing)}")
        return {"healthy": True, "topic_count": len(topics)}
    finally:
        consumer.close()


def check_connector(connect_url: str, connector_name: str) -> dict[str, object]:
    url = f"{connect_url.rstrip('/')}/connectors/{connector_name}/status"
    response = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=10)).request("GET", url)
    if response.status != 200:
        raise RuntimeError(f"Kafka Connect status returned HTTP {response.status}")
    payload = json.loads(response.data.decode("utf-8"))
    connector_state = payload.get("connector", {}).get("state")
    task_states = [task.get("state") for task in payload.get("tasks", [])]
    if connector_state != "RUNNING" or not task_states or set(task_states) != {"RUNNING"}:
        raise RuntimeError("Debezium connector or one of its tasks is not RUNNING")
    return {"healthy": True, "connector_state": connector_state, "task_count": len(task_states)}


def consumer_group_lag(
    bootstrap_servers: str, group_id: str, topics: tuple[str, ...]
) -> dict[str, object]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
            "session.timeout.ms": 6000,
        }
    )
    try:
        metadata = consumer.list_topics(timeout=10)
        partitions = [
            TopicPartition(topic, partition)
            for topic in topics
            for partition in sorted(metadata.topics[topic].partitions)
        ]
        committed = consumer.committed(partitions, timeout=10)
        total_lag = 0
        details: list[dict[str, Any]] = []
        for item in committed:
            low, high = consumer.get_watermark_offsets(
                TopicPartition(item.topic, item.partition), timeout=10, cached=False
            )
            committed_offset = item.offset if item.offset >= 0 else low
            lag = max(high - committed_offset, 0)
            total_lag += lag
            details.append({"topic": item.topic, "partition": item.partition, "lag": lag})
        return {"total_lag": total_lag, "partitions": details}
    finally:
        consumer.close()


def cdc_manifest_freshness(path: Path, *, now: datetime | None = None) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError("CDC consumer manifest is unavailable")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT max(committed_at), count(*) FROM cdc_batch_manifest WHERE status = 'COMMITTED'"
        ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("CDC consumer manifest has no committed batches")
    committed_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).astimezone(UTC)
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "freshness_seconds": max((observed_at - committed_at).total_seconds(), 0),
        "committed_batch_count": int(row[1]),
        "last_committed_at": committed_at.isoformat(),
    }
