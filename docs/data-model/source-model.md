# Source Model Skeleton

## Modeling rules

- Monetary values use fixed-precision decimal types, never floating point.
- Business event time, source commit time, ingestion time, and processing time remain separate.
- Business keys come from source domains; ingestion identifiers do not replace them.
- Every source declares ownership, classification, retention, update semantics, and late-data behavior.
- Source payloads are immutable in Bronze; corrections create new source or processing records.

## Planned operational entities

| Entity | Business key | Intended grain | Change mode |
| --- | --- | --- | --- |
| `customers` | `customer_id` | One current OLTP row per customer | CDC insert/update/delete |
| `accounts` | `account_id` | One current OLTP row per account | CDC insert/update/delete |
| `merchants` | `merchant_id` | One current OLTP row per merchant | CDC plus optional batch snapshot |
| `payment_transactions` | `transaction_id` | One current OLTP row per payment | CDC insert/update/delete |
| `transaction_events` | `event_id` | One immutable status or domain event | Kafka append |
| `refunds` | `refund_id` | One current refund record | CDC and domain events |
| `settlement_files` | `partner_id`, `file_id` | One immutable received file | Batch manifest |
| `settlement_lines` | `partner_id`, `file_id`, `line_number` | One line in one partner file | Batch append |

## Required audit metadata

CDC and event records should preserve, where applicable:

- Source system, schema, table or event type, and schema version.
- Kafka topic, partition, offset, event key, and producer timestamp.
- PostgreSQL LSN and source transaction identifier.
- Source event time, source commit time, ingestion time, and processing time.
- Ingestion batch ID, object key, checksum, and contract validation result.

## Relationships to refine in Phase 1

- A customer can own one or more accounts.
- A merchant can accept many payment transactions.
- A payment transaction has one or more lifecycle events and zero or more refunds.
- A settlement file contains many settlement lines.
- A settlement line can match zero, one, or multiple internal transactions when duplicates or partner
  aggregation rules apply.

Detailed columns, constraints, enumerations, and contract versions will be specified with the source
schema and data generator in Phase 1.
