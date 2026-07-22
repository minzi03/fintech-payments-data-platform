# Silver Data Model

## Dataset grains

| Dataset | Grain | Purpose |
| --- | --- | --- |
| `cdc/history` | One valid Kafka CDC event | Audit, replay, SCD2 input, incident analysis |
| `cdc/latest_all` | Latest row per entity business key, including deletes | Authoritative state projection |
| `cdc/current` | Latest non-deleted row per business key | Active operational view |
| `cdc/events/transaction_events` | One source `event_id` | Append-only payment lifecycle evidence |
| `settlements` | One accepted partner settlement row | Typed reconciliation input for a later phase |
| `rejections` | One processing quality failure | Confidential remediation evidence |
| `unresolved_references` | One late/unresolved relationship | Non-blocking reference follow-up |

Business keys are `customer_id`, `account_id`, `merchant_id`, `transaction_id`, `event_id`, and
`refund_id` for the six CDC entities respectively.

## CDC history contract

History retains event/business identity, operation/snapshot/delete/tombstone flags, canonical
before/after/payload JSON, LSN and transaction ID, source/connector/Kafka/ingestion/processing clocks,
Kafka topic/partition/offset, processing run, and source/Silver schema versions. All timestamps are
UTC Arrow timestamps. A missing business key is rejected rather than producing an unauditable row.

## Entity state contracts

All state schemas are explicit PyArrow schemas and include `is_deleted`, source LSN, Kafka
coordinates, effective event time, processing time/run, and schema versions.

- Customers derive `first_name` and `last_name` deterministically from the OLTP `full_name`, while
  retaining external reference, email, country, status, and source timestamps.
- Accounts preserve `balance` as `decimal128(18,2)` and retain the customer relationship.
- Merchants retain business/external references, category/country, settlement currency, and status.
- Payment transactions preserve source relationships, type/channel, `amount decimal128(18,2)`,
  status/references, and lifecycle timestamps.
- Transaction events retain event version/status transition, event/producer clocks, trace/source,
  canonical JSON payload, and append-only grain.
- Refunds preserve transaction relationship, `amount decimal128(18,2)`, currency, lifecycle, and
  partner evidence.

## Decimal and timestamp decoding

PostgreSQL precise Decimal values may arrive in Bronze as a human-readable decimal string or Kafka
Connect's base64 two's-complement unscaled bytes. Silver decodes with known scale 2 into Python
`Decimal`, validates precision 18, and writes Arrow `decimal128(18,2)`; float input is rejected.

Debezium temporal values under `time.precision.mode=microseconds` are normalized from epoch
microseconds. ISO-8601 values require an explicit offset. Source event, connector, Kafka, ingestion,
and processing time remain distinct.

## Settlement contract

Silver settlement rows contain the eleven `settlement-v1` business fields plus source file,
checksum, source row, ingestion run, processing run, and processing time. Amount, fee, and net are
`decimal128(18,2)` and transaction timestamp is UTC. This dataset is not a reconciliation result.

## Physical format

Parquet 2.6 with ZSTD compression, explicit column order, dictionaries, and statistics. Each object
is immutable under a run-partitioned path. Parquet metadata contains only contract/version data;
object metadata uses a non-secret operational allowlist.
