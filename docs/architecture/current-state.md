# Current State

## Status

The repository contains the **Phase 2 - Banking Partner Settlement Batch Ingestion Foundation**
implementation. Phase 1 remains locally verified against PostgreSQL 16.4; Phase 2 is verified with
Docker-independent filesystem and SQLite integration tests.

Implemented in source control:

- A single PostgreSQL 16 Docker Compose service with a health check, named volume, and read-only
  ordered initialization scripts.
- The `payments` OLTP schema, reference data, constraints, indexes, timestamp triggers, and immutable
  transaction-event enforcement.
- A deterministic Python generator with environment/CLI configuration, Decimal money, UTC
  timestamps, transactional persistence, and controlled invalid/duplicate probes.
- Docker-independent unit tests and explicitly marked PostgreSQL integration tests.
- Phase 1 schema and local-operation documentation.
- A versioned settlement CSV contract and deterministic partner scenario fixtures.
- Filename/checksum discovery, file/record validation, SQLite manifest lifecycle, immutable local
  Bronze copy, metadata sidecars, rejected-record evidence, file quarantine, and replay protection.
- A settlement CLI with single-file/directory, dry-run, and strict-rejection modes.

Not implemented:

- Kafka, Debezium, Schema Registry, MinIO, Airflow, or distributed processing.
- Executable CDC, domain-event, Silver, or reconciliation pipelines.
- MinIO-backed Bronze; Phase 2 Bronze is a local storage adapter only.
- Snowflake objects, executable dbt models, dashboards, alerts, lineage, or platform observability.
- Production deployment, high availability, backup/restore, TLS, or secret-manager integration.

The target architecture describes future boundaries only. It is not evidence that those components
are deployed.
