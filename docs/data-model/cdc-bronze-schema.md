# CDC Bronze Parquet Schema

## Contract

Phase 5 writes `cdc-bronze-v1` using PyArrow 25 and Parquet format version 2.6 with ZSTD compression.
Column order and types are explicit; pandas and schema inference are not used.

| Column | Arrow type | Nullable | Semantics |
| --- | --- | --- | --- |
| `event_id` | string | no | SHA-256 of topic, partition, and offset |
| `entity_name` | string | no | Debezium source table; topic suffix for tombstones |
| `operation` | string | no | `r`, `c`, `u`, `d`, or internal tombstone marker `t` |
| `is_snapshot` | bool | no | True only for `op=r` |
| `is_deleted` | bool | no | True for delete envelopes and tombstones |
| `is_tombstone` | bool | no | True only when Kafka value is null |
| `event_key_json` | large_string | yes | Deterministic JSON key; base64 wrapper if key is non-JSON |
| `before_json` | large_string | yes | Canonical previous row; retained for delete |
| `after_json` | large_string | yes | Canonical current row for snapshot/create/update |
| `source_metadata_json` | large_string | yes | Complete Debezium source metadata |
| `source_lsn` | int64 | yes | PostgreSQL WAL LSN reported by Debezium |
| `source_tx_id` | int64 | yes | PostgreSQL transaction identifier when present |
| `source_ts_ms` | int64 | yes | Source commit/event clock in epoch milliseconds |
| `connector_ts_ms` | int64 | yes | Debezium processing clock in epoch milliseconds |
| `kafka_topic` | string | no | Original CDC topic |
| `kafka_partition` | int32 | no | Original Kafka partition |
| `kafka_offset` | int64 | no | Original Kafka offset; downstream deduplication coordinate |
| `kafka_message_ts_ms` | int64 | yes | Kafka message timestamp in epoch milliseconds |
| `ingested_at` | timestamp(us, UTC) | no | Stable manifest creation time used for replay bytes |
| `schema_version` | string | no | Bronze projection version |
| `raw_event_json` | large_string | yes | Original non-tombstone UTF-8 Kafka value |

## JSON and Decimal rules

Derived JSON uses sorted keys, compact separators, UTF-8, and `Decimal -> string`; binary floating
point is never used for financial values. Under the Phase 4 connector's `precise` mode, PostgreSQL
`NUMERIC(18,2)` remains a schema-described Kafka Connect Decimal byte value in the raw envelope.
Phase 5 preserves that representation rather than projecting money into an inferred Arrow float.

The `raw_event_json` column intentionally duplicates information available through envelope
columns. The cost is larger Bronze storage; the benefit is byte-level forensic replay and future
schema re-projection before a registry/compatibility policy exists. Tombstones have null raw values.
Phase 6 may introduce a normalized money column after decoding scale/precision under a versioned
Silver contract.

## Timestamp convention

All epoch fields retain their named clock rather than being conflated. `ingested_at` is a
timezone-aware UTC Arrow timestamp. `event_date` is used only in the object path and derives from
source time, then connector time, then Kafka time. No local timezone is written to Parquet.

## Delete and snapshot semantics

- Snapshot `r`: `is_snapshot=true`, `after_json` populated, never treated as create.
- Create `c`: `after_json` populated.
- Update `u`: `before_json` and `after_json` populated.
- Delete `d`: `before_json` populated, `after_json` null, `is_deleted=true`.
- Tombstone `t`: null value, key retained, both row images null, `is_tombstone=true`.

Heartbeat messages are validated and acknowledged but are not business rows in this Parquet
contract. Poison records use the separate confidential quarantine contract described in the
consumer architecture.

## Classification and compatibility

CDC Bronze is confidential because before/after/raw fields may contain customer and financial data.
Default inspection returns only schema, operation counts, coordinates, manifest state, and checksums.
The schema version changes for incompatible column or semantic changes. Phase 5 has no external
Schema Registry and does not claim cross-version compatibility beyond preserving the raw event.
