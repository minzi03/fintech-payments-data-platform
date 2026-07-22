# Current State

## Status through Phase 4

The repository now has independently executable batch and CDC intake foundations:

```text
Payment generator -> PostgreSQL 16 payments OLTP
                         |
                         v logical WAL
                    Debezium Connect -> Kafka CDC topics

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

Compose contains exactly `postgres`, `minio`, `minio-init`, `kafka`, `kafka-connect`, and
`connector-init`. Local storage remains the default for batch tests. SQLite continues to own mutable
batch manifest state; MinIO owns immutable artifacts; Kafka owns the CDC log and Connect state.

## Runtime boundaries

- Phase 4 ends at Kafka topics. No application consumes CDC records or writes them to Bronze.
- Source rows are unchanged by the CDC implementation. Only PostgreSQL runtime WAL settings, a
  connector role, grants, and an explicit publication are added.
- Kafka uses three partitions per CDC topic locally, replication factor one, seven-day retention,
  and key-based ordering within a partition. This is not an HA topology.
- Kafka Connect retains source offsets in compacted Kafka internal topics. Connector deletion does
  not automatically remove its replication slot or Kafka history.

## Not implemented

- CDC-to-MinIO consumer, Silver CDC application/deduplication, DLQ, or replay coordinator.
- Business event topics such as `payment_completed`; Phase 4 contains database CDC topics only.
- Reconciliation classification, Parquet/table formats, Airflow, Spark/Flink, Snowflake, executable
  dbt models, dashboards, catalog, lineage, metrics, or alerting.
- TLS/SASL/ACLs, external secrets, distributed Kafka/Connect/PostgreSQL, replication factor greater
  than one, backups, disaster recovery, production retention sizing, or capacity benchmarks.

The target architecture and roadmap label all later components as planned; their mention is not an
implementation claim.
