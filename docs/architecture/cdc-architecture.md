# PostgreSQL CDC Architecture

## Status and boundary

The Phase 4 transport remains implemented and unchanged. Phase 5 now consumes its table topics into
immutable Bronze while preserving the envelope:

```text
payments OLTP -> PostgreSQL logical WAL -> Debezium -> Kafka CDC topics
                                                        |
                                                        v
                                            reliable consumer -> MinIO Bronze
```

The transport still ends at Kafka as an independently testable boundary. The Phase 5 consumer is a
separate process and does not alter connector semantics. No component builds Silver data, classifies
settlements, or emits business-event topics.

## Components

| Component | Local implementation | Responsibility |
| --- | --- | --- |
| PostgreSQL | `postgres:16.4-bookworm` | Authoritative source; logical WAL and `pgoutput` |
| Kafka | `apache/kafka:4.3.1` | Single-node KRaft broker; persistent ordered topic log |
| Kafka Connect | `quay.io/debezium/connect:3.6.0.Final` | Debezium runtime and durable source offsets |
| `connector-init` | Pinned Python 3.11 image | Reconcile role/publication and connector after health checks |
| Connector config | `payments-postgres.json` | Versioned capture/type/topic behavior |

PostgreSQL, Kafka, and Kafka Connect are long running. `connector-init` is one-shot and must exit
zero. Compose waits for PostgreSQL and Kafka health before Connect, and for Connect health before
bootstrap.

## Source prerequisites and least privilege

PostgreSQL starts with `wal_level=logical`, bounded `max_replication_slots`, and bounded
`max_wal_senders`. Bootstrap creates the environment-named connector role separately from the
application administrator and continuously verifies:

- LOGIN and REPLICATION are enabled;
- SUPERUSER, CREATEDB, and CREATEROLE are disabled;
- CONNECT on the configured database and USAGE on `payments` are granted;
- SELECT is granted only on the six captured business tables;
- an explicit publication contains those same six tables.

The publication and slot use validated lowercase identifiers. Debezium uses `pgoutput`, never the
PostgreSQL application superuser. Existing Phase 1 volumes are supported because bootstrap runs at
service startup rather than only in `/docker-entrypoint-initdb.d`.

## Topic convention and local policy

Topic prefix is `fintech.cdc`; Debezium derives:

| Table | Topic | Primary-key semantics |
| --- | --- | --- |
| `customers` | `fintech.cdc.payments.customers` | `customer_id` |
| `accounts` | `fintech.cdc.payments.accounts` | `account_id` |
| `merchants` | `fintech.cdc.payments.merchants` | `merchant_id` |
| `payment_transactions` | `fintech.cdc.payments.payment_transactions` | `transaction_id` |
| `transaction_events` | `fintech.cdc.payments.transaction_events` | `event_id` |
| `refunds` | `fintech.cdc.payments.refunds` | `refund_id` |

Local defaults are three partitions, replication factor one, `delete` cleanup, and seven-day
retention. A primary-key JSON record key selects the partition, so changes for one unchanged key are
ordered within that partition. There is no global ordering across keys/partitions. Transaction
metadata can describe database transaction ordering, but downstream consumers must retain Kafka
topic/partition/offset as their transport position.

Kafka Connect config, offset, and status topics are compacted internal topics in the same persistent
Kafka volume. With transaction metadata enabled, Debezium also emits the connector-level
`fintech.cdc.transaction` metadata topic; it is not a table/business-event topic. The broker
auto-creates only required local topics. Replication factor one is a local development constraint,
not a production recommendation.

## Connector decisions

- `snapshot.mode=initial`: capture existing rows on first start, then stream WAL. Populate the source
  before first registration to exercise snapshot `r` records.
- `publication.autocreate.mode=disabled`: bootstrap owns the explicit publication.
- `slot.drop.on.stop=false`: ordinary restarts/deletes do not discard source position.
- `decimal.handling.mode=precise`: PostgreSQL `NUMERIC(18,2)` is Kafka Connect Decimal bytes plus
  scale/precision schema, never `double`.
- `time.precision.mode=microseconds`: Debezium preserves microsecond-capable time schemas;
  `TIMESTAMPTZ` values are UTC `ZonedTimestamp` strings.
- JSON converters with schemas enabled: record key/value schemas and the complete Debezium envelope
  remain present. No flattening SMT removes source metadata.
- Tombstones and transaction metadata are enabled; heartbeat bounds inactive-source offset/WAL
  progress behavior.
- PostgreSQL does not require an external schema-history topic; its connector derives relational
  schemas from the source and embeds Kafka Connect schema in each JSON value.

## Bootstrap, restart, and replay

The REST client waits with bounded exponential backoff, validates configuration, and then creates,
updates, or returns `unchanged`. It treats Connect's internal `name` field and masked password
correctly and never prints connector configuration. Invalid configs and non-retryable HTTP failures
are explicit, redacted failures.

On restart, Connect loads the stored source offset and continues from its retained PostgreSQL slot.
Kafka messages already published remain in their topics; diagnostic inspection starts from the
beginning without a durable consumer group. At-least-once boundaries still require downstream
deduplication by source position/business key in Phase 5/6.

Phase 5 adds a separate consumer group with automatic commit/store disabled. It retains the full
envelope plus Kafka coordinates in explicit-schema Parquet and commits only after immutable upload,
checksum verification, and manifest `UPLOADED`. See `cdc-bronze-ingestion.md` for the crash matrix,
replay, rebalance, DLQ, and object identity contract.

## Security and limitations

Kafka and Connect host ports bind to `127.0.0.1`; containers use their private Compose network.
Credentials come only from environment variables, are hidden from dataclass representations, and
are redacted from errors. The inspection CLI outputs primary keys and operational metadata, never
full `before`/`after` rows.

Local broker/Connect/PostgreSQL traffic is plaintext and has no SASL/ACL/TLS. There is one broker,
one Connect worker, one connector task, no capacity benchmark, and no production WAL/retention
alerting. Poison CDC records now use private MinIO quarantine, but there is no centralized alerting or
distributed recovery coordinator. These are known limitations, not hidden production claims.
