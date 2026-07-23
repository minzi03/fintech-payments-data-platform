# Current State

## Status through Phase 7

The repository now has independently executable batch and CDC intake foundations:

```text
Payment generator -> PostgreSQL 16 payments OLTP
                         |
                         v logical WAL
                    Debezium Connect -> Kafka CDC topics
                                             |
                                             v
                                 reliable Python CDC consumer
                                  |          |             |
                                  v          v             v
                            Parquet       SQLite       quarantine
                            MinIO Bronze  manifest     poison evidence
                                  |
                                  v
                         PyArrow Silver processor
                         | history/latest/current
                         | settlements/quality
                         v
                    private MinIO Silver
                              |
                              v
                   Airflow 3 orchestration
                   | metadata PostgreSQL
                   ` control schema

Partner settlement CSV -> contract validation -> SQLite manifest
                              |
                              v
                      SettlementStorage
                         |         |
                         v         v
                  local files   private MinIO
                  Bronze/QA     Bronze/QA buckets
```

Implemented:

- Phase 0 repository standards, safe configuration, documentation, CI, and quality gates.
- Phase 1 constrained PostgreSQL OLTP source and deterministic Decimal/UTC generator.
- Phase 2 settlement contract, fixtures, checksum/manifest lifecycle, validation, local immutable
  Bronze/quarantine, and batch CLI.
- Phase 3 local/MinIO storage adapters, private bucket bootstrap, immutable conditional writes,
  integrity metadata, retries, and real MinIO integration tests.
- Phase 4 PostgreSQL logical WAL settings, dedicated non-superuser replication role, explicit
  publication for six business tables, Kafka 4 KRaft, Debezium Connect, versioned connector config,
  persistent offsets/config/status topics, idempotent connector reconciliation, full schema-enabled
  envelopes, redacted bounded inspection, and real CDC integration tests.
- Phase 5 manual-commit Kafka consumer, envelope validation, deterministic partition/range
  micro-batching, explicit Arrow schema, ZSTD Parquet, immutable MinIO Bronze publication, SQLite
  batch lifecycle, MinIO poison quarantine, safe rebalance/shutdown, replay/recovery, and real
  Kafka/MinIO integration tests.
- Phase 6 local/MinIO incremental discovery, explicit CDC/settlement readers, Decimal/UTC
  normalization, event history, latest-all/current/delete semantics, append-only transaction events,
  confidential quality and unresolved-reference outputs, immutable Silver Parquet, SQLite lineage,
  skip/force/dry-run controls, and real MinIO integration tests.
- Phase 7 Airflow 3 LocalExecutor with dedicated PostgreSQL metadata, required API/scheduler/DAG
  processor services, four bounded DAGs, centralized pipeline/task/quality/backfill state,
  aggregate quality gates, safe retries, validated dry-run backfill, and redacted failure handling.

Compose retains the Phase 1-6 services and adds Airflow metadata PostgreSQL, init, loopback API/UI,
scheduler, and Airflow 3 DAG processor. Local storage remains available for lightweight tests.
Component SQLite manifests own file/batch/object state; PostgreSQL `control` owns cross-pipeline
state; Airflow metadata owns scheduling; MinIO owns immutable artifacts.

## Runtime boundaries

- Phase 7 ends at orchestration and centralized operational state. No Gold/reconciliation
  classification, warehouse load, dbt execution, or BI publication runs.
- Source rows are unchanged by the CDC implementation. Only PostgreSQL runtime WAL settings, a
  connector role, grants, and an explicit publication are added.
- Kafka uses three partitions per CDC topic locally, replication factor one, seven-day retention,
  and key-based ordering within a partition. This is not an HA topology.
- Kafka Connect retains source offsets in compacted Kafka internal topics. Connector deletion does
  not automatically remove its replication slot or Kafka history.

## Not implemented

- Distributed/table-format Silver compaction, registry-driven schema evolution, or derived business
  event topics.
- Business event topics such as `payment_completed`; Phase 4 contains database CDC topics only.
- Reconciliation classification, table formats, Spark/Flink, Snowflake, executable
  dbt models, dashboards, catalog, lineage, metrics, or alerting.
- TLS/SASL/ACLs, external secrets, distributed Kafka/Connect/PostgreSQL, replication factor greater
  than one, backups, disaster recovery, production retention sizing, or capacity benchmarks.

The target architecture and roadmap label all later components as planned; their mention is not an
implementation claim.
