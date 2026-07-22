# Implementation Roadmap

Phases are dependency-ordered and independently testable. Planned technology names are boundaries,
not claims of implementation.

## Status and dependency flow

```text
Phase 0 Foundation                         [implemented]
        |
        v
Phase 1 PostgreSQL OLTP + generator        [implemented and locally verified]
        |
        +----------------------+----------------------+
        |                      |                      |
        v                      v                      v
Phase 2 Batch ingestion   Phase 3 CDC to Bronze   Phase 4 Event streaming
      [implemented]             [planned]                [planned]
        |                      |                      |
        +----------------------+----------------------+
                               |
                               v
                   Phase 5 Silver + data quality
                               |
                               v
                   Phase 6 Snowflake + dbt
                               |
                               v
                   Phase 7 Reconciliation product
                               |
                               v
                Phase 8 Orchestration + observability
                               |
                               v
                  Phase 9 Dashboards + hardening
```

## Phase 0 - Project Foundation

**Status:** Implemented.

Repository boundaries, safe configuration patterns, Python quality tooling, CI, and initial business
and architecture documentation were established without runtime services.

## Phase 1 - PostgreSQL OLTP Source and Realistic Generator

**Status:** Implemented and locally verified against a clean PostgreSQL 16.4 volume.

**Depends on:** Phase 0.

**Deliverables:** PostgreSQL payment schema/reference data/indexes, local Compose service,
deterministic Decimal/UTC generator, valid lifecycle scenarios, controlled invalid/duplicate probes,
unit tests, optional PostgreSQL integration tests, and a local runbook.

**Independent acceptance:** clean-volume initialization and health succeed; the CLI persists related
valid data; constraints reject invalid data; deterministic/precision/lifecycle unit tests pass; lint,
format, default tests, Compose validation, and PostgreSQL integration tests pass.

**Deliberately deferred at Phase 1 completion:** partner settlement ingestion, CDC, Kafka,
storage/processing, orchestration, warehouse, dashboards, and observability.

## Phase 2 - Batch Settlement Ingestion

**Status:** Implemented and locally verified with filesystem and SQLite integration tests.

**Depends on:** stable Phase 1 internal transaction keys plus an approved partner-file contract.

**Deliverables:** versioned settlement contract, deterministic fixtures, file discovery,
checksum/schema/record validation, SQLite manifests, local immutable Bronze, quarantine, structured
results, and replay by deterministic content ID.

**Independent acceptance:** replay is idempotent; changed content under a reused name is detected;
file-level failures are quarantined; partial row errors retain evidence; failed Bronze writes never
produce `PROCESSED`.

**Deliberately deferred:** partner transport, MinIO, reconciliation, Silver models, orchestration,
observability, and production concurrency controls.

## Phase 3 - PostgreSQL CDC to Bronze

**Depends on:** stable Phase 1 schema and an approved CDC contract.

**Planned deliverables:** PostgreSQL publication settings, Debezium, Kafka, schema handling, durable
consumer, deterministic Bronze keys, offset-safe commits, DLQ, and metrics.

**Independent acceptance:** restart/rebalance/failure scenarios lose no accepted source changes and
produce replay-safe output.

## Phase 4 - Payment Event Streaming

**Depends on:** stable Phase 1 event semantics; can progress independently of Phase 2.

**Planned deliverables:** versioned event contracts, partitioning/ordering rules, validation,
retry/DLQ, late-event policy, operational aggregates, and latency measurements.

## Phase 5 - Silver Processing and Data Quality

**Depends on:** at least one stable Bronze source from Phases 2-4.

**Planned deliverables:** typed conformed records, CDC application, deduplication, quarantine,
referential checks, reconciliation counts, and publish gates.

## Phase 6 - Snowflake and dbt Analytics

**Depends on:** stable Phase 5 schemas.

**Planned deliverables:** least-privilege Snowflake objects, dbt staging/intermediate/marts, SCD2
dimensions, late-arriving-safe facts, contracts, tests, docs, and exposures.

## Phase 7 - Reconciliation Data Product

**Depends on:** partner settlement Silver data and internal payment facts.

**Planned deliverables:** versioned matching rules, classified result fact, unmatched workflow fields,
Finance mart, and daily report.

## Phase 8 - Orchestration and Observability

**Depends on:** executable pipelines from Phases 2-7.

**Planned deliverables:** Airflow DAGs, control state, lineage, freshness/volume/quality metrics,
alerts, recovery runbooks, and backfill commands.

## Phase 9 - Dashboards and Release Hardening

**Depends on:** governed operations and reconciliation marts.

**Planned deliverables:** Operations/Finance/Executive dashboards, semantic definitions, end-to-end
tests, deployment environments, promotion controls, and measured SLA evidence.
