"""Explicit PyArrow schemas for Silver history, state, settlement, and quality datasets."""

from __future__ import annotations

import pyarrow as pa

UTC_TS = pa.timestamp("us", tz="UTC")
MONEY = pa.decimal128(18, 2)

LINEAGE_FIELDS = [
    pa.field("is_deleted", pa.bool_(), nullable=False),
    pa.field("source_lsn", pa.int64()),
    pa.field("kafka_topic", pa.string(), nullable=False),
    pa.field("kafka_partition", pa.int32(), nullable=False),
    pa.field("kafka_offset", pa.int64(), nullable=False),
    pa.field("effective_event_time", UTC_TS, nullable=False),
    pa.field("processed_at", UTC_TS, nullable=False),
    pa.field("processing_run_id", pa.string(), nullable=False),
    pa.field("source_schema_version", pa.string(), nullable=False),
    pa.field("silver_schema_version", pa.string(), nullable=False),
]


def _state_schema(fields: list[pa.Field], entity: str) -> pa.Schema:
    return pa.schema(
        [*fields, *LINEAGE_FIELDS],
        metadata={b"contract": f"fintech-silver-{entity}".encode(), b"schema_version": b"1"},
    )


ENTITY_SCHEMAS: dict[str, pa.Schema] = {
    "customers": _state_schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("external_customer_ref", pa.string()),
            pa.field("first_name", pa.string()),
            pa.field("last_name", pa.string()),
            pa.field("email", pa.string()),
            pa.field("country_code", pa.string()),
            pa.field("status", pa.string()),
            pa.field("created_at", UTC_TS),
            pa.field("updated_at", UTC_TS),
        ],
        "customers",
    ),
    "accounts": _state_schema(
        [
            pa.field("account_id", pa.string(), nullable=False),
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("account_number", pa.string()),
            pa.field("currency", pa.string()),
            pa.field("balance", MONEY),
            pa.field("status", pa.string()),
            pa.field("created_at", UTC_TS),
            pa.field("updated_at", UTC_TS),
        ],
        "accounts",
    ),
    "merchants": _state_schema(
        [
            pa.field("merchant_id", pa.string(), nullable=False),
            pa.field("merchant_code", pa.string()),
            pa.field("external_reference", pa.string()),
            pa.field("merchant_name", pa.string()),
            pa.field("category_code", pa.string()),
            pa.field("country_code", pa.string()),
            pa.field("settlement_currency", pa.string()),
            pa.field("status", pa.string()),
            pa.field("created_at", UTC_TS),
            pa.field("updated_at", UTC_TS),
        ],
        "merchants",
    ),
    "payment_transactions": _state_schema(
        [
            pa.field("transaction_id", pa.string(), nullable=False),
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("account_id", pa.string(), nullable=False),
            pa.field("destination_account_id", pa.string()),
            pa.field("merchant_id", pa.string()),
            pa.field("transaction_type", pa.string()),
            pa.field("payment_channel", pa.string()),
            pa.field("amount", MONEY),
            pa.field("currency", pa.string()),
            pa.field("status", pa.string()),
            pa.field("partner_reference", pa.string()),
            pa.field("idempotency_key", pa.string()),
            pa.field("failure_code", pa.string()),
            pa.field("requested_at", UTC_TS),
            pa.field("completed_at", UTC_TS),
            pa.field("failed_at", UTC_TS),
            pa.field("created_at", UTC_TS),
            pa.field("updated_at", UTC_TS),
        ],
        "payment_transactions",
    ),
    "transaction_events": _state_schema(
        [
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("transaction_id", pa.string(), nullable=False),
            pa.field("event_type", pa.string()),
            pa.field("event_version", pa.int32()),
            pa.field("previous_status", pa.string()),
            pa.field("new_status", pa.string()),
            pa.field("event_time", UTC_TS),
            pa.field("producer_time", UTC_TS),
            pa.field("trace_id", pa.string()),
            pa.field("source_system", pa.string()),
            pa.field("event_payload_json", pa.large_string()),
            pa.field("created_at", UTC_TS),
        ],
        "transaction_events",
    ),
    "refunds": _state_schema(
        [
            pa.field("refund_id", pa.string(), nullable=False),
            pa.field("transaction_id", pa.string(), nullable=False),
            pa.field("amount", MONEY),
            pa.field("currency", pa.string()),
            pa.field("status", pa.string()),
            pa.field("reason_code", pa.string()),
            pa.field("partner_reference", pa.string()),
            pa.field("requested_at", UTC_TS),
            pa.field("completed_at", UTC_TS),
            pa.field("created_at", UTC_TS),
            pa.field("updated_at", UTC_TS),
        ],
        "refunds",
    ),
}

HISTORY_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("entity_name", pa.string(), nullable=False),
        pa.field("business_key", pa.string(), nullable=False),
        pa.field("operation", pa.string(), nullable=False),
        pa.field("is_snapshot", pa.bool_(), nullable=False),
        pa.field("is_deleted", pa.bool_(), nullable=False),
        pa.field("is_tombstone", pa.bool_(), nullable=False),
        pa.field("before_json", pa.large_string()),
        pa.field("after_json", pa.large_string()),
        pa.field("business_payload_json", pa.large_string()),
        pa.field("source_lsn", pa.int64()),
        pa.field("source_tx_id", pa.int64()),
        pa.field("source_ts", UTC_TS),
        pa.field("connector_ts", UTC_TS),
        pa.field("kafka_message_ts", UTC_TS),
        pa.field("kafka_topic", pa.string(), nullable=False),
        pa.field("kafka_partition", pa.int32(), nullable=False),
        pa.field("kafka_offset", pa.int64(), nullable=False),
        pa.field("event_time", UTC_TS, nullable=False),
        pa.field("ingested_at", UTC_TS, nullable=False),
        pa.field("processed_at", UTC_TS, nullable=False),
        pa.field("processing_run_id", pa.string(), nullable=False),
        pa.field("source_schema_version", pa.string(), nullable=False),
        pa.field("silver_schema_version", pa.string(), nullable=False),
    ],
    metadata={b"contract": b"fintech-silver-cdc-history", b"schema_version": b"1"},
)

SETTLEMENT_SCHEMA = pa.schema(
    [
        pa.field("partner_id", pa.string(), nullable=False),
        pa.field("settlement_date", pa.date32(), nullable=False),
        pa.field("settlement_reference", pa.string(), nullable=False),
        pa.field("partner_transaction_reference", pa.string(), nullable=False),
        pa.field("internal_transaction_id", pa.string()),
        pa.field("transaction_timestamp", UTC_TS, nullable=False),
        pa.field("amount", MONEY, nullable=False),
        pa.field("currency", pa.string(), nullable=False),
        pa.field("settlement_status", pa.string(), nullable=False),
        pa.field("fee_amount", MONEY, nullable=False),
        pa.field("net_amount", MONEY, nullable=False),
        pa.field("source_file_name", pa.string(), nullable=False),
        pa.field("source_checksum", pa.string(), nullable=False),
        pa.field("source_row_number", pa.int32(), nullable=False),
        pa.field("ingestion_run_id", pa.string()),
        pa.field("processing_run_id", pa.string(), nullable=False),
        pa.field("processed_at", UTC_TS, nullable=False),
    ],
    metadata={b"contract": b"fintech-silver-settlement", b"schema_version": b"1"},
)

REJECTION_SCHEMA = pa.schema(
    [
        pa.field("source_object_uri", pa.string(), nullable=False),
        pa.field("source_event_id", pa.string()),
        pa.field("entity_name", pa.string(), nullable=False),
        pa.field("business_key", pa.string()),
        pa.field("error_code", pa.string(), nullable=False),
        pa.field("error_message", pa.string(), nullable=False),
        pa.field("raw_reference", pa.string(), nullable=False),
        pa.field("processing_run_id", pa.string(), nullable=False),
        pa.field("rejected_at", UTC_TS, nullable=False),
    ],
    metadata={b"classification": b"confidential", b"schema_version": b"1"},
)

UNRESOLVED_REFERENCE_SCHEMA = pa.schema(
    [
        pa.field("entity_name", pa.string(), nullable=False),
        pa.field("business_key", pa.string(), nullable=False),
        pa.field("reference_entity", pa.string(), nullable=False),
        pa.field("reference_key", pa.string(), nullable=False),
        pa.field("reference_field", pa.string(), nullable=False),
        pa.field("classification", pa.string(), nullable=False),
        pa.field("source_event_id", pa.string(), nullable=False),
        pa.field("processing_run_id", pa.string(), nullable=False),
        pa.field("observed_at", UTC_TS, nullable=False),
    ],
    metadata={b"contract": b"fintech-silver-unresolved-reference", b"schema_version": b"1"},
)


def entity_schema(entity_name: str) -> pa.Schema:
    try:
        return ENTITY_SCHEMAS[entity_name]
    except KeyError as error:
        raise ValueError(f"Unsupported Silver entity: {entity_name}") from error
