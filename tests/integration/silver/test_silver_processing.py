"""Real MinIO Bronze-to-Silver acceptance tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common.storage import sha256_file
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.models import InputObject, OutputType, ProcessingStatus
from processing.silver.schemas import MONEY
from tests.unit.processing.silver.conftest import bronze_row, customer_payload

pytestmark = [pytest.mark.integration, pytest.mark.silver_integration]


def _upload_cdc(environment, entity: str, rows: list[dict[str, object]]) -> InputObject:
    settings, backend, _client, _manifest, _storage, _processor, runtime = environment
    identity = uuid4().hex
    path = runtime / f"{identity}.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=CDC_ARROW_SCHEMA), path, compression="zstd")
    offsets = [int(row["kafka_offset"]) for row in rows]
    partition = int(rows[0]["kafka_partition"])
    topic = str(rows[0]["kafka_topic"])
    key = (
        f"cdc/entity={entity}/event_date=2026-07-22/topic={topic}/partition={partition}/"
        f"offset_start={min(offsets)}/offset_end={max(offsets)}/batch_id={identity}.parquet"
    )
    stored = backend.put_immutable(
        bucket=settings.storage.bronze_bucket,
        object_key=key,
        source=path,
        checksum_sha256=sha256_file(path),
        content_type="application/vnd.apache.parquet",
        metadata={
            "artifact_type": "cdc_bronze_parquet",
            "entity_name": entity,
            "schema_version": "cdc-bronze-v1",
            "record_count": len(rows),
        },
    )
    return InputObject(
        stored.uri,
        stored.bucket,
        stored.object_key,
        stored.checksum_sha256,
        stored.size_bytes,
        stored.metadata,
    )


def _output(run, kind: OutputType):
    return next(output for output in run.outputs if output.output_type is kind)


def test_real_minio_cdc_entities_decimal_timestamp_and_append_only(silver_environment) -> None:
    settings, _backend, _client, manifest, storage, processor, _runtime = silver_environment
    base = 50_000_000 + int(uuid4().hex[:6], 16)
    customer_id, account_id, merchant_id, transaction_id = (
        f"c-{uuid4().hex}",
        f"a-{uuid4().hex}",
        f"m-{uuid4().hex}",
        f"t-{uuid4().hex}",
    )
    customer = _upload_cdc(
        silver_environment,
        "customers",
        [bronze_row("customers", customer_id, customer_payload(customer_id), offset=base)],
    )
    merchant_payload = {
        "merchant_id": merchant_id,
        "merchant_code": "M001",
        "external_reference": "EXT-M001",
        "merchant_name": "Merchant",
        "category_code": "5411",
        "country_code": "VN",
        "settlement_currency": "USD",
        "status": "ACTIVE",
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_600_000_000,
    }
    account_payload = {
        "account_id": account_id,
        "customer_id": customer_id,
        "account_number": "ACC001",
        "currency": "USD",
        "balance": "123.45",
        "status": "ACTIVE",
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_600_000_000,
    }
    transaction_payload = {
        "transaction_id": transaction_id,
        "customer_id": customer_id,
        "account_id": account_id,
        "destination_account_id": None,
        "merchant_id": merchant_id,
        "transaction_type": "MERCHANT_PAYMENT",
        "payment_channel": "API",
        "amount": "99.95",
        "currency": "USD",
        "status": "COMPLETED",
        "partner_reference": "P-1",
        "idempotency_key": f"idem-{uuid4().hex}",
        "failure_code": None,
        "requested_at": 1_753_185_600_000_000,
        "completed_at": 1_753_185_601_000_000,
        "failed_at": None,
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_601_000_000,
    }
    event_id = f"e-{uuid4().hex}"
    event_payload = {
        "event_id": event_id,
        "transaction_id": transaction_id,
        "event_type": "PAYMENT_COMPLETED",
        "event_version": 1,
        "previous_status": "PENDING",
        "new_status": "COMPLETED",
        "event_time": 1_753_185_601_000_000,
        "producer_time": 1_753_185_601_100_000,
        "trace_id": f"trace-{uuid4().hex}",
        "source_system": "generator",
        "event_payload": {"safe": "value"},
        "created_at": 1_753_185_601_100_000,
    }
    refund_id = f"r-{uuid4().hex}"
    refund_payload = {
        "refund_id": refund_id,
        "transaction_id": transaction_id,
        "amount": "10.05",
        "currency": "USD",
        "status": "COMPLETED",
        "reason_code": "CUSTOMER_REQUEST",
        "partner_reference": "RP-1",
        "requested_at": 1_753_185_602_000_000,
        "completed_at": 1_753_185_603_000_000,
        "created_at": 1_753_185_602_000_000,
        "updated_at": 1_753_185_603_000_000,
    }
    inputs = [
        customer,
        _upload_cdc(
            silver_environment,
            "merchants",
            [bronze_row("merchants", merchant_id, merchant_payload, offset=base)],
        ),
        _upload_cdc(
            silver_environment,
            "accounts",
            [bronze_row("accounts", account_id, account_payload, offset=base)],
        ),
        _upload_cdc(
            silver_environment,
            "payment_transactions",
            [bronze_row("payment_transactions", transaction_id, transaction_payload, offset=base)],
        ),
        _upload_cdc(
            silver_environment,
            "transaction_events",
            [bronze_row("transaction_events", event_id, event_payload, offset=base)],
        ),
        _upload_cdc(
            silver_environment,
            "refunds",
            [bronze_row("refunds", refund_id, refund_payload, offset=base)],
        ),
    ]
    results = [processor.process_cdc(item) for item in inputs]

    assert all(result.status is ProcessingStatus.COMPLETED for result in results)
    account_run = manifest.get(results[2].run_id or "")
    transaction_run = manifest.get(results[3].run_id or "")
    event_run = manifest.get(results[4].run_id or "")
    assert account_run and transaction_run and event_run
    accounts = storage.read_table(_output(account_run, OutputType.CURRENT).object_uri)
    transactions = storage.read_table(_output(transaction_run, OutputType.CURRENT).object_uri)
    events = storage.read_table(_output(event_run, OutputType.EVENTS).object_uri)
    assert accounts.schema.field("balance").type == MONEY
    assert accounts["balance"].to_pylist() == [Decimal("123.45")]
    assert transactions["amount"].to_pylist() == [Decimal("99.95")]
    assert transactions.schema.field("processed_at").type.tz == "UTC"
    assert events.num_rows == 1
    assert settings.storage.silver_bucket == "fintech-silver"


def test_delete_latest_all_current_and_idempotent_force_reprocess(silver_environment) -> None:
    _settings, _backend, _client, manifest, storage, processor, _runtime = silver_environment
    customer_id = f"delete-{uuid4().hex}"
    base = 70_000_000 + int(uuid4().hex[:5], 16)
    created = _upload_cdc(
        silver_environment,
        "customers",
        [bronze_row("customers", customer_id, customer_payload(customer_id), offset=base)],
    )
    deleted = _upload_cdc(
        silver_environment,
        "customers",
        [
            bronze_row(
                "customers",
                customer_id,
                customer_payload(customer_id),
                operation="d",
                offset=base + 1,
            ),
            bronze_row(
                "customers",
                customer_id,
                None,
                operation="t",
                offset=base + 2,
            ),
        ],
    )
    processor.process_cdc(created)
    first = processor.process_cdc(deleted)
    skipped = processor.process_cdc(deleted)
    forced = processor.process_cdc(deleted, force_reprocess=True)

    run = manifest.get(first.run_id or "")
    assert run
    latest = storage.read_table(_output(run, OutputType.LATEST_ALL).object_uri)
    current = storage.read_table(_output(run, OutputType.CURRENT).object_uri)
    assert latest["is_deleted"].to_pylist()[-1] is True
    assert latest["kafka_offset"].to_pylist()[-1] == base + 2
    assert customer_id not in set(current["customer_id"].to_pylist())
    assert skipped.skipped and forced.run_id != first.run_id
    assert forced.rejected_record_count == 0


def test_invalid_decimal_writes_rejection_without_failed_run(silver_environment) -> None:
    _settings, _backend, _client, manifest, storage, processor, _runtime = silver_environment
    account_id = f"invalid-{uuid4().hex}"
    payload = {
        "account_id": account_id,
        "customer_id": "unknown-customer",
        "account_number": "BAD",
        "currency": "USD",
        "balance": "not-decimal",
        "status": "ACTIVE",
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_600_000_000,
    }
    item = _upload_cdc(
        silver_environment,
        "accounts",
        [bronze_row("accounts", account_id, payload, offset=80_000_000 + int(uuid4().hex[:5], 16))],
    )

    result = processor.process_cdc(item)
    run = manifest.get(result.run_id or "")

    assert run and run.status is ProcessingStatus.COMPLETED
    rejection_output = _output(run, OutputType.REJECTIONS)
    rejected = storage.read_table(rejection_output.object_uri)
    assert "INVALID_DECIMAL" in rejected["error_code"].to_pylist()
    assert rejected.schema.metadata[b"classification"] == b"confidential"


def test_real_settlement_bronze_to_silver(silver_environment) -> None:
    settings, backend, _client, manifest, storage, processor, runtime = silver_environment
    partner = f"S{uuid4().hex[:5].upper()}"
    source = generate_settlement_fixtures(
        FixtureConfig(runtime / partner, partner, date(2026, 7, 22), 42)
    )["valid"]
    key = (
        f"settlements/partner_id={partner}/settlement_date=2026-07-22/"
        f"ingestion_date=2026-07-22/checksum={sha256_file(source)}/{source.name}"
    )
    stored = backend.put_immutable(
        bucket=settings.storage.bronze_bucket,
        object_key=key,
        source=source,
        checksum_sha256=sha256_file(source),
        content_type="text/csv",
        metadata={
            "partner_id": partner,
            "source_file_name": source.name,
            "ingestion_run_id": f"ingest-{partner}",
            "schema_version": "settlement-v1",
        },
    )
    item = InputObject(
        stored.uri,
        stored.bucket,
        stored.object_key,
        stored.checksum_sha256,
        stored.size_bytes,
        stored.metadata,
    )

    result = processor.process_settlement(item)
    run = manifest.get(result.run_id or "")

    assert run and run.status is ProcessingStatus.COMPLETED
    table = storage.read_table(_output(run, OutputType.SETTLEMENTS).object_uri)
    assert table.num_rows == 5
    assert table["amount"].to_pylist()[0].as_tuple().exponent == -2
    assert table.schema.field("transaction_timestamp").type.tz == "UTC"
