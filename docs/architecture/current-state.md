# Current State

## Status through Phase 3

The repository implements a local production-like source and ingestion foundation:

```text
Payment generator -> PostgreSQL 16 payments OLTP

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
- Phase 1 PostgreSQL OLTP objects and deterministic Decimal/UTC payment generator.
- Phase 2 versioned settlement contract, deterministic fixtures, discovery/checksum, file/record
  validation, SQLite manifest lifecycle, partial rejection, local immutable Bronze/quarantine, and
  batch CLI.
- Phase 3 typed storage configuration, shared immutable interface, local and MinIO adapters,
  deterministic object layout, allowlisted metadata, conditional collision-safe writes, bounded
  retry/timeouts, private bucket bootstrap, CLI backend selection, and real MinIO integration tests.

Compose contains exactly three services: `postgres`, `minio`, and one-shot `minio-init`. Local files
remain the default backend so unit tests and lightweight ingestion do not require Docker. SQLite
continues to own mutable manifest state; MinIO owns immutable data artifacts only.

## Not implemented

- Kafka, Debezium, Schema Registry, CDC consumers, or payment-event streaming.
- Silver processing, reconciliation classification, cross-file business deduplication, or Parquet.
- Airflow, Snowflake, executable dbt models, dashboards, catalog, lineage, metrics, or alerting.
- Partner SFTP/API/PGP transport, malware scanning, or source-file deletion.
- Distributed locking, MinIO object lock/versioning, TLS, external secrets, scoped service accounts,
  replication, backup/restore, retention automation, or high availability.

The target architecture and roadmap describe planned boundaries only; they are not evidence that
later technologies are deployed.
