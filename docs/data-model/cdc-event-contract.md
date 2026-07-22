# CDC Event Contract

## Contract status

Implemented transport contract for Phase 4 PostgreSQL CDC topics and consumed unchanged by the
Phase 5 Bronze service. The versioned connector template and database schema determine the embedded
record schema; `cdc-bronze-v1` preserves the envelope and transport metadata in Parquet.

## Record key

Kafka keys use schema-enabled JSON:

```json
{
  "schema": {"type": "struct", "fields": ["primary key schema..."]},
  "payload": {"transaction_id": "UUID"}
}
```

The payload contains only the table primary key (`customer_id`, `account_id`, `merchant_id`,
`transaction_id`, `event_id`, or `refund_id`). Key stability supplies per-key partition ordering.

## Record value and envelope

Non-tombstone values use schema-enabled Debezium JSON:

```text
schema
payload
  before
  after
  source
  transaction
  op
  ts_ms / ts_us / ts_ns
```

`before` and `after` use the source table schema and are nullable according to the operation.
`source` includes connector name/version, database, schema, table, snapshot marker, source commit
timestamp, PostgreSQL transaction ID, and LSN. The outer timestamps describe connector processing
time. Transaction metadata, when populated, adds transaction ID and ordering fields.

## Operation semantics

| `op` | Meaning | `before` | `after` |
| --- | --- | --- | --- |
| `r` | Initial snapshot read | `null` | Existing source row |
| `c` | Committed insert/create | `null` | New row |
| `u` | Committed update | Previous row | New row |
| `d` | Committed delete | Deleted row | `null` |

With `tombstones.on.delete=true`, an allowed source delete produces a `d` envelope followed by a
Kafka tombstone: the same key with a `null` value. Consumers must not confuse the tombstone with a
malformed record. `transaction_events` cannot update/delete because the Phase 1 database trigger
enforces immutability; delete verification therefore uses a relationship-free customer probe.

## Decimal and timestamp contract

Money remains exact:

```text
schema.type = bytes
schema.name = org.apache.kafka.connect.data.Decimal
schema.parameters.scale = 2
schema.parameters.connect.decimal.precision = 18
payload value = base64-encoded signed unscaled integer bytes
```

Consumers must decode `unscaled * 10^-scale` into a fixed-precision Decimal. They must never cast the
payload through binary `float`/`double`.

PostgreSQL `TIMESTAMPTZ` columns become `io.debezium.time.ZonedTimestamp` UTC strings with `Z`.
Source and envelope clocks additionally expose integer `ts_ms`, `ts_us`, and (connector-dependent)
`ts_ns`. Consumers must distinguish source commit time, connector processing time, and Kafka broker
position.

## Metadata preserved by Phase 5

The implemented Bronze consumer retains:

- deterministic key/envelope JSON plus the original non-tombstone UTF-8 value;
- connector name and Debezium version;
- source database/schema/table, LSN, transaction ID, snapshot flag, and source timestamps;
- envelope operation and processing timestamps;
- Kafka topic, partition, offset, and record timestamp;
- ingestion time, deterministic event/batch IDs, object checksum, and Bronze schema version.

`topic + partition + offset` is the unique Kafka transport position. Source LSN/transaction metadata
supports database ordering/audit but must not be assumed globally unique without the source identity.
Connector-level BEGIN/END ordering metadata is also emitted to `fintech.cdc.transaction`; Phase 5
does not subscribe to it because the consumer uses an explicit six-table allowlist.

## Schema handling and compatibility

Phase 4 uses no flattening SMT and no external Schema Registry. Each JSON record carries its Kafka
Connect schema; connector/source evolution can therefore change the embedded schema. Phase 5 stores
the original JSON value alongside projected metadata. Phase 6 will define compatibility, projection,
defaults, and breaking-change quarantine based on actual downstream requirements.

## Classification and data sensitivity

CDC row values are confidential internal financial/customer data. Default topic inspection returns
only the primary key, operation, table, snapshot flag, LSN, timestamps, transaction ID, partition,
and offset. Full row payloads are reserved for controlled testing/troubleshooting and must not be
pasted into tickets or logs.
