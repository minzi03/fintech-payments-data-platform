# Implementation Roadmap

Phases are dependency-ordered and independently testable. A technology named in a future phase is a
plan, not an implementation claim.

```text
Phase 0 Foundation                                      [implemented]
  -> Phase 1 PostgreSQL OLTP + generator                [implemented]
      -> Phase 2 Settlement batch ingestion             [implemented]
          -> Phase 3 Shared local/MinIO Bronze storage  [implemented]
              -> Phase 4 PostgreSQL CDC + Kafka         [implemented]
                  -> Phase 5 CDC consumer to Bronze     [implemented]
                      -> Phase 6 Silver + data quality  [implemented]
                          -> Phase 7 Airflow             [planned]
                              -> Phase 8 Snowflake/dbt  [planned]
                                  -> Phase 9 Reconciliation product
                                      -> Phase 10 Operations analytics/hardening
```

## Completed phases

### Phase 0 - Project Foundation

Repository boundaries, Python 3.11 tooling, safe environment patterns, CI, documentation skeletons,
and placeholders were established without runtime pipeline implementations.

### Phase 1 - PostgreSQL OLTP Source and Generator

The constrained payments schema, reference data, indexes, lifecycle enforcement, deterministic
synthetic generator, tests, and local PostgreSQL runbook were implemented and verified.

### Phase 2 - Banking Partner Settlement Batch Ingestion

The `settlement-v1` contract, scenario fixtures, filename/checksum discovery, file/record quality,
SQLite manifest, local immutable Bronze/quarantine, structured CLI results, and replay rules were
implemented. Reconciliation was deliberately deferred.

### Phase 3 - MinIO-backed Shared Bronze Storage

**Depends on:** Phase 2's stable storage boundary and manifest ordering.

**Implemented:** a small storage interface, local and MinIO adapters, content-addressed Bronze and
run-addressed quarantine keys, SHA-256 metadata, collision protection, bounded client failures,
private idempotent bucket bootstrap, backend selection, local/real-service tests, CI job, and
operations documentation.

**Independent acceptance:** local behavior remains valid; MinIO raw bytes/checksum agree; identical
content is idempotent; conflicting content cannot overwrite; invalid schemas are quarantine-only;
partial row failures write Bronze and rejection evidence; failed upload cannot become `PROCESSED`.

**Deliberately deferred:** distributed locking, production identity/TLS/retention, CDC, downstream
format conversion, reconciliation, orchestration, warehouse, BI, and observability.

### Phase 4 - PostgreSQL CDC Infrastructure with Debezium and Kafka

**Depends on:** Phase 1's stable business schema and Phase 3's independently validated Bronze
boundary. Phase 4 does not connect those paths yet.

**Implemented:** logical WAL settings, dedicated non-superuser connector role, explicit publication,
single-node Kafka KRaft, Debezium Kafka Connect, persistent internal state, versioned connector
template, precise Decimal and microsecond timestamp settings, schema-enabled full envelopes,
idempotent create/update bootstrap, connector lifecycle scripts, metadata-only topic inspection, and
real-service tests for snapshot/create/update/delete/restart/event/refund semantics.

**Independent acceptance:** all six topics exist; connector and task are `RUNNING`; bootstrap returns
`unchanged` on replay; initial snapshot emits `r`; later writes emit `c`/`u`/`d`; tombstones are
distinguished; Decimal remains logical bytes; timestamps, source LSN, source transaction ID, Kafka
partition, and offset remain available.

**Deliberately deferred:** CDC consumer, MinIO publication, transforms/SMTs that discard envelope
metadata, Schema Registry, business event topics, reconciliation, orchestration, analytics, and
production Kafka security/HA.

### Phase 5 - CDC Consumer to Shared Bronze

**Depends on:** Phase 4 CDC topic semantics and Phase 3 immutable storage.

**Implemented:** manual commit/store control, wrapper/direct Debezium parsing, snapshot/create/update/
delete/tombstone handling, deterministic Kafka event and range identity, partition-aware contiguous
micro-batches, explicit-schema ZSTD Parquet, immutable MinIO publication, SQLite batch manifest,
private poison quarantine, bounded retry, rebalance/shutdown flush, payload-safe inspection, and
upload-before-commit replay/recovery tests.

**Independent acceptance:** exact Kafka coordinates are preserved; upload/checksum/`UPLOADED`
precede synchronous `offset_end + 1`; replay reuses identical objects; collision does not overwrite;
poison offsets advance only after quarantine; committed groups do not reprocess on restart.

**Deliberately deferred:** Silver merging/deduplication, distributed locks/control store, production
security/HA/retention, schema registry/governance, orchestration, analytics, and observability.

### Phase 6 - Silver Processing and Data Quality

**Implemented:** typed settlement/payment records, explicit PyArrow schemas, precise Decimal/UTC
normalization, CDC history and current/latest state, delete/tombstone behavior, coordinate
deduplication/order guards, unresolved references, quality outputs, immutable Silver storage,
incremental SQLite lineage, force/dry-run controls, and real MinIO tests. No distributed engine was
needed at the current scale.

## Planned phases

### Phase 7 - Airflow Orchestration

Schedules, dependencies, PostgreSQL control state, retries/backfills, runbooks, and failure signals
around already executable pipelines.

### Phase 8 - Snowflake and dbt

Least-privilege warehouse objects, staging/intermediate/marts, SCD Type 2 dimensions, incremental
facts, tests, documentation, and exposures after Silver contracts stabilize.

### Phase 9 - Settlement Reconciliation Product

Versioned match rules, classified reconciliation facts, unmatched workflows, Finance marts, and
daily evidence. This is the first phase that labels mismatch candidates.

### Phase 10 - Near-real-time Analytics and Hardening

Operations marts/dashboard, SLAs, metrics/alerts, lineage/catalog choices, security hardening,
performance evidence, deployment/promotion controls, and end-to-end recovery tests.
