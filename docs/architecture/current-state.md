# Current State

## Status

The repository contains the **Phase 1 - PostgreSQL OLTP Source and Realistic Data Generator**
implementation. It has been locally verified against PostgreSQL 16.4; CI keeps the Docker-independent
quality gate, while database integration tests remain an explicit developer command.

Implemented in source control:

- A single PostgreSQL 16 Docker Compose service with a health check, named volume, and read-only
  ordered initialization scripts.
- The `payments` OLTP schema, reference data, constraints, indexes, timestamp triggers, and immutable
  transaction-event enforcement.
- A deterministic Python generator with environment/CLI configuration, Decimal money, UTC
  timestamps, transactional persistence, and controlled invalid/duplicate probes.
- Docker-independent unit tests and explicitly marked PostgreSQL integration tests.
- Phase 1 schema and local-operation documentation.

Not implemented:

- Kafka, Debezium, Schema Registry, MinIO, Airflow, or distributed processing.
- Executable CDC, batch-settlement, domain-event, Bronze, or Silver pipelines.
- Snowflake objects, executable dbt models, dashboards, alerts, lineage, or platform observability.
- Production deployment, high availability, backup/restore, TLS, or secret-manager integration.

The target architecture describes future boundaries only. It is not evidence that those components
are deployed.
