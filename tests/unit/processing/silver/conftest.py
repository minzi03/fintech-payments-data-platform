"""Deterministic Silver unit-test fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common.storage import LocalStorageBackend, sha256_file
from ingestion.cdc_consumer.models import deterministic_event_id
from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.bronze_reader import BronzeReader
from processing.silver.config import SilverSettings
from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.models import InputObject
from processing.silver.processor import SilverProcessor
from processing.silver.storage import SilverStorage

NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
TOPIC_PREFIX = "fintech.cdc.payments"


def bronze_row(
    entity: str,
    business_key: str,
    payload: dict[str, object] | None,
    *,
    operation: str = "c",
    offset: int = 0,
    partition: int = 0,
) -> dict[str, object]:
    topic = f"{TOPIC_PREFIX}.{entity}"
    key_name = {
        "customers": "customer_id",
        "accounts": "account_id",
        "merchants": "merchant_id",
        "payment_transactions": "transaction_id",
        "transaction_events": "event_id",
        "refunds": "refund_id",
    }[entity]
    before = payload if operation == "d" else None
    after = payload if operation in {"r", "c", "u"} else None
    return {
        "event_id": deterministic_event_id(topic, partition, offset),
        "entity_name": entity,
        "operation": operation,
        "is_snapshot": operation == "r",
        "is_deleted": operation in {"d", "t"},
        "is_tombstone": operation == "t",
        "event_key_json": json.dumps({"payload": {key_name: business_key}}, sort_keys=True),
        "before_json": json.dumps(before, sort_keys=True) if before is not None else None,
        "after_json": json.dumps(after, sort_keys=True) if after is not None else None,
        "source_metadata_json": json.dumps({"table": entity, "lsn": 100 + offset}),
        "source_lsn": 100 + offset if operation != "t" else None,
        "source_tx_id": 42 if operation != "t" else None,
        "source_ts_ms": 1_753_185_600_000 if operation != "t" else None,
        "connector_ts_ms": 1_753_185_600_100 if operation != "t" else None,
        "kafka_topic": topic,
        "kafka_partition": partition,
        "kafka_offset": offset,
        "kafka_message_ts_ms": 1_753_185_600_200,
        "ingested_at": NOW,
        "schema_version": "cdc-bronze-v1",
        "raw_event_json": json.dumps({"payload": {"op": operation}}),
    }


def write_bronze(
    root: Path,
    rows: list[dict[str, object]],
    *,
    entity: str,
    name: str = "part.parquet",
) -> InputObject:
    key = (
        f"cdc/entity={entity}/event_date=2026-07-22/"
        f"topic={TOPIC_PREFIX}.{entity}/partition=0/{name}"
    )
    path = root / Path(*key.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=CDC_ARROW_SCHEMA), path, compression="zstd")
    return InputObject(
        uri=str(path),
        bucket="fintech-bronze",
        object_key=key,
        checksum_sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        metadata={"schema_version": "cdc-bronze-v1"},
    )


def customer_payload(customer_id: str, *, status: str = "ACTIVE") -> dict[str, object]:
    return {
        "customer_id": customer_id,
        "external_customer_ref": f"EXT-{customer_id}",
        "full_name": "Ada Lovelace",
        "email": "safe@example.test",
        "country_code": "VN",
        "status": status,
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_600_000_000,
    }


def make_processor(tmp_path: Path, run_ids: list[str] | None = None):
    environment = {
        "STORAGE_BACKEND": "local",
        "SETTLEMENT_BRONZE_DIR": str(tmp_path / "bronze"),
        "SETTLEMENT_QUARANTINE_DIR": str(tmp_path / "quarantine"),
        "SILVER_LOCAL_ROOT": str(tmp_path / "silver"),
        "SILVER_MANIFEST_DB": str(tmp_path / "control" / "silver.sqlite3"),
        "SILVER_TEMP_DIR": str(tmp_path / "temp"),
        "SILVER_SETTLEMENT_CONTRACT": str(
            Path(__file__).resolve().parents[4] / "contracts" / "batch" / "settlement_v1.yml"
        ),
    }
    settings = SilverSettings.from_env(environment)
    backend = LocalStorageBackend(
        {
            settings.storage.bronze_bucket: settings.storage.local_bronze_root,
            settings.storage.quarantine_bucket: settings.storage.local_quarantine_root,
            settings.storage.silver_bucket: settings.storage.local_silver_root,
        }
    )
    manifest = SqliteProcessingManifest(settings.manifest_path)
    identifiers = iter(run_ids or [f"run-{index}" for index in range(1, 20)])
    storage = SilverStorage(backend, bucket=settings.storage.silver_bucket)
    processor = SilverProcessor(
        settings=settings,
        reader=BronzeReader(backend),
        storage=storage,
        manifest=manifest,
        clock=lambda: NOW,
        run_id_factory=lambda: next(identifiers),
    )
    return processor, manifest, storage, settings
