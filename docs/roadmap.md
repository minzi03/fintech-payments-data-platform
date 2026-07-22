# Implementation Roadmap

Phases are dependency-ordered and independently testable. A phase can start only when its required
upstream contracts are stable; optional platform components must not block a core business slice.

## Dependency flow

```text
Phase 0 Foundation
        |
        v
Phase 1 Domain model + PostgreSQL OLTP + generator
        |
        +----------------------+----------------------+
        |                      |                      |
        v                      v                      v
Phase 2 Batch ingestion   Phase 3 CDC to Bronze   Phase 4 Event streaming
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

**Objective:** Establish repository boundaries, documentation, safe configuration patterns, local
quality tooling, and CI.

**Acceptance evidence:** Ruff, formatting, Pytest/coverage, YAML validation, and Docker Compose
configuration validation pass; no service or pipeline is deployed.

## Phase 1 - Domain model and realistic sources

**Depends on:** Phase 0.

**Deliverables:** PostgreSQL payment schema, versioned event and batch contracts, deterministic data
generator, normal and failure scenarios, and contract tests.

**Independent acceptance:** Generated records satisfy declared contracts; money uses decimal types;
duplicate, invalid, late, out-of-order, update, delete, and replay cases are reproducible by seed.

## Phase 2 - Batch settlement ingestion

**Depends on:** Phase 1 settlement contract.

**Deliverables:** Partner-file discovery, checksum and schema validation, ingestion manifests,
quarantine, immutable Bronze objects, and replay by batch ID.

**Independent acceptance:** Re-running the same unchanged file creates no duplicate accepted data;
changed content under a reused name is detected; rejected files never become processed.

## Phase 3 - PostgreSQL CDC to Bronze

**Depends on:** Phase 1 source schema and CDC contract.

**Deliverables:** PostgreSQL, Debezium, Kafka, schema handling, durable Python micro-batch consumer,
deterministic object keys, offset-safe commits, DLQ, and metrics.

**Independent acceptance:** Restart, rebalance, and upload-before-commit scenarios lose no accepted
records and create no duplicate Silver business keys.

## Phase 4 - Payment domain-event streaming

**Depends on:** Phase 1 event contracts; can progress independently of Phase 2.

**Deliverables:** Versioned lifecycle events, partitioning and ordering rules, validation, retry/DLQ,
watermarks, late-event policy, operational aggregates, and latency metrics.

**Independent acceptance:** Duplicate and out-of-order fixtures produce deterministic transaction
state and measured end-to-end latency.

## Phase 5 - Silver processing and data quality

**Depends on:** At least one stable Bronze source from Phases 2-4.

**Deliverables:** Typed conformed records, CDC application, deduplication, quarantine, referential
checks, reconciliation counts, and publish gates.

**Independent acceptance:** Contract, duplicate, null, amount, status, and referential-integrity tests
pass; quarantined rows retain actionable reason codes.

## Phase 6 - Snowflake and dbt analytics

**Depends on:** Phase 5 schemas.

**Deliverables:** Least-privilege Snowflake objects, dbt sources, staging/intermediate/marts, SCD2
dimensions, late-arriving-safe incremental facts, model contracts, tests, docs, and exposures.

**Independent acceptance:** SCD validity intervals do not overlap, one current dimension version
exists, and reruns preserve fact uniqueness.

## Phase 7 - Reconciliation data product

**Depends on:** Phase 2 settlement Silver data and Phase 6 internal payment facts.

**Deliverables:** Versioned matching rules, classified result fact, unmatched-item workflow fields,
Finance mart, and daily report.

**Independent acceptance:** Every eligible internal and settlement item is accounted for by a match or
documented mismatch class; totals reconcile by partner and currency.

## Phase 8 - Orchestration and observability

**Depends on:** Executable pipelines from Phases 2-7.

**Deliverables:** Airflow DAGs, control tables, lineage events, freshness/volume/quality metrics,
infrastructure metrics, alerts, recovery runbooks, and backfill commands.

**Independent acceptance:** Failed upstream work blocks publication, recovery alerts clear correctly,
and a controlled backfill is auditable.

## Phase 9 - Business dashboards and release hardening

**Depends on:** Governed operations and reconciliation marts.

**Deliverables:** Operations, Finance, and Executive dashboards; semantic definitions; integration and
end-to-end tests; deployment environments; promotion and rollback controls.

**Independent acceptance:** Dashboard totals agree with certified marts, drill-downs preserve business
keys, target SLAs are benchmarked, and observed metrics are reported separately from targets.
