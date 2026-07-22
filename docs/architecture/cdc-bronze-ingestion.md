# CDC Bronze Ingestion Architecture

## Status and boundary

Implemented in Phase 5. The service consumes the six explicit Debezium table topics and ends at
immutable Parquet objects in the private Bronze bucket:

```text
Kafka CDC topic -> parse/validate -> topic-partition micro-batch -> Parquet -> MinIO -> commit offset
                                      |                         |
                                      v                         v
                                SQLite manifest          quarantine DLQ
```

Phase 5 itself has no Silver projection or current-state merge. Phase 6 now consumes this immutable
contract without changing the Phase 5 commit protocol; reconciliation and orchestration remain
later. Kafka may replay records, so the contract is **effectively once at the
immutable object boundary**, not exactly once end to end.

## Reliability invariants

1. Both `enable.auto.commit` and `enable.auto.offset.store` are `false`.
2. A batch contains one topic, one partition, one entity, one event date, one schema version, and a
   contiguous offset range.
3. The object key and `batch_id` are deterministic from Kafka coordinates and schema version.
4. Parquet is uploaded and verified before the manifest becomes `UPLOADED`.
5. Kafka receives only `offset_end + 1`, scoped to the successfully uploaded partition.
6. The manifest becomes `COMMITTED` only after the synchronous Kafka commit succeeds.
7. A replay verifies the existing object checksum. Identical content is idempotent; different bytes
   at the same key raise an immutable collision and do not advance the source offset.

If two groups race on a first write, the losing conditional writer reads the winner's allowlisted
first-ingestion timestamp, rebuilds once, and accepts only byte-identical output. A second mismatch is
treated as a genuine collision.

This ordering permits duplicate delivery after a crash but prevents an acknowledged source offset
from pointing past missing Bronze data. Downstream processing must deduplicate by
`kafka_topic + kafka_partition + kafka_offset`.

## Identity and object layout

```text
event_id = sha256("<topic>:<partition>:<offset>")
batch_id = sha256("<topic>:<partition>:<offset_start>:<offset_end>:<schema_version>")

s3://fintech-bronze/
  cdc/entity=<entity>/event_date=YYYY-MM-DD/
  topic=<topic>/partition=<partition>/
  offset_start=<start>/offset_end=<end>/
  batch_id=<batch_id>.parquet
```

Object metadata contains only operational allowlisted values: source/entity, topic, partition,
range, record count, consumer group, schema version, checksum, ingestion time, and snapshot/delete/
tombstone flags. Keys, row payloads, local absolute paths, credentials, and connection strings are
excluded.

## Batch manifest

SQLite is the Phase 5 local transactional control store behind a narrow manifest interface. Its
lifecycle is:

```text
COLLECTING -> SERIALIZING -> UPLOADING -> UPLOADED -> COMMITTED
                    |             |
                    +----------> FAILED -> SERIALIZING (bounded retry/replay)
```

`UPLOADED` is deliberately recoverable. On assignment, if Kafka already stores a next offset at or
beyond the range end, an `UPLOADED` row is promoted to `COMMITTED`. If Kafka is behind, its replay
reconstructs the deterministic batch, verifies/reuses the object, commits, and closes the manifest.

## Rebalance and shutdown

Pending state is partition keyed and memory bounded. Size, time, an entity/date/range boundary,
partition revoke, explicit bounded run, or graceful SIGINT/SIGTERM triggers a flush. Revoke flushes
only revoked partitions. A fatal validation, upload, manifest, DLQ, or commit error does not flush
later pending data because doing so could skip the unresolved offset.

The consumer stops polling on a termination signal, persists valid pending ranges, commits only
their uploaded offsets, closes the Kafka client, and removes temporary Parquet files. The configured
shutdown timeout is an operational bound; container/process managers must allow at least that grace
period.

## Poison policy

Phase 5 selects one DLQ path: private MinIO quarantine. Malformed JSON, missing Debezium payload or
source coordinates, unsupported operations, invalid delete/create shape, and record serialization
failures are confidential poison evidence. The key is deterministic from consumer group plus source
coordinates. The
JSON object stores base64 key/value evidence plus a safe error code; logs never print those bytes.

The source offset advances only after the immutable quarantine write is confirmed. A quarantine
failure leaves the source offset uncommitted and therefore retryable. `CDC_DLQ_TOPIC` is the logical
DLQ name used in paths/metadata; Phase 5 does not create a second Kafka producer flow.

## Implemented, planned, and out of scope

Implemented: exact topic allowlist, wrapper/direct envelope parsing, `r/c/u/d`, tombstone and
heartbeat handling, explicit Arrow schema, ZSTD Parquet, local/MinIO backend selection, bounded
retry, recovery, payload-safe inspection, and opt-in Kafka/MinIO acceptance tests.

Planned: PostgreSQL control schema and distributed leases, production Kafka/MinIO authentication,
metrics/alerts, schema compatibility governance, retention, and controlled repair tooling.

Downstream Phase 6 now implements Silver state/history/quality. Spark/Flink, Airflow, dbt,
Snowflake, reconciliation, BI, and an observability platform remain out of scope.
