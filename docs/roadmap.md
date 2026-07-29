# Implementation history and bounded roadmap

## Purpose

This document separates completed implementation history from the current portfolio-finalization
track and uncommitted future direction. A deferred item is not scheduled delivery and is not an
implementation claim.

Current architecture is authoritative in
[docs/architecture/current-state.md](architecture/current-state.md). Approved external wording is
defined by [docs/architecture/claims.md](architecture/claims.md).

## Status summary

```text
Data platform Phases 0–7             COMPLETED
Portal security/runtime Sprints 01–05 COMPLETED
Sprint 06 tasks S06-01 through S06-05 COMPLETED
FF-01 Validation isolation            COMPLETED
FF-02 Security disposition            COMPLETED
FF-03 Canonical entrypoint             COMPLETED
FF-04 Architecture and claims          CURRENT
FF-05 Demo and evidence                NEXT
FF-06 Verification/publication         PENDING
```

Feature development is frozen. FF-04 through FF-06 may improve documentation, evidence,
verification, and publishing decisions; they may not add product or architecture scope.

## Completed data-platform history

| Phase                           | Outcome                                  | Implemented boundary                                                     |
| ------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------ |
| Phase 0 — Foundation            | Repository/tooling baseline              | Python tooling, CI, documentation, safe configuration                    |
| Phase 1 — Payments source       | Constrained OLTP and deterministic data  | PostgreSQL source model, lifecycle constraints, generator                |
| Phase 2 — Settlement intake     | Versioned replay-safe file boundary      | Contract, fixtures, validation, SQLite manifest, local Bronze/quarantine |
| Phase 3 — Shared object storage | Immutable local/MinIO boundary           | Storage interface, private buckets, checksums, conditional writes        |
| Phase 4 — CDC infrastructure    | PostgreSQL-to-Kafka row-change transport | WAL/pgoutput, Debezium, Kafka topics, connector reconciliation           |
| Phase 5 — CDC to Bronze         | Manual-consumer publication boundary     | Micro-batches, Parquet, manifest, quarantine, upload-before-commit       |
| Phase 6 — Silver and quality    | Typed derived data and evidence          | History/latest/current, settlement outputs, quality, lineage             |
| Phase 7 — Airflow orchestration | Scheduling and cross-pipeline control    | Four DAGs, Airflow metadata, PostgreSQL control state                    |

The executable data path ends at Silver and Airflow/control evidence. Warehouse, dbt,
reconciliation products, Gold, and dashboards were not delivered by Phases 0–7.

## Completed Portal security/runtime history

| Track                          | Outcome                                                                        |
| ------------------------------ | ------------------------------------------------------------------------------ |
| Sprint 01 — truthful readiness | PostgreSQL/OIDC adapters, fail-closed required dependency aggregation          |
| Sprint 02 — telemetry          | OpenTelemetry metrics/tracing, correlated structured logs, exporters           |
| Sprint 03 — provider lifecycle | Refresh, rotation/reuse detection, fencing, logout, revocation, crypto-erasure |
| Sprint 04 — abuse protection   | Trusted client identity, Redis atomic enforcement, fallback, penalties         |
| Sprint 05 — audit/maintenance  | Transactional outbox, delivery worker, dead letter, bounded maintenance        |

These sprints created a separate Portal security runtime. They did not add Portal operational
adapters for Kafka, MinIO, Airflow, or Silver.

## Completed Sprint 06 hardening

| Task                                    | Outcome                                                                       | Explicitly not granted                            |
| --------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------- |
| S06-01 — reproducible artifacts         | Hash-locked Python inputs, pinned actions/base images, build manifest         | Guaranteed byte-identical OCI digest, publication |
| S06-02 — secret-provider boundary       | Vendor-neutral references/provider contract and environment adapter           | Concrete KMS or cloud secret manager              |
| S06-03 — production configuration model | Frozen role/profile-aware configuration and strict validation                 | Production security authorization                 |
| S06-04 — container hardening            | Non-root/read-only first-party images, capabilities/network/resource controls | Production deployment topology                    |
| S06-05 — security scanning              | Five pinned scanner families, fail-closed policy, bounded exceptions          | Vulnerability-free or supply-chain attestation    |

## Finalization track

| Gate  | Objective                                     | Status    | Allowed change class                    |
| ----- | --------------------------------------------- | --------- | --------------------------------------- |
| FF-01 | Isolate destructive migration validation      | Completed | Validation safety                       |
| FF-02 | Remediate/disposition image findings          | Completed | Critical security/release evidence      |
| FF-03 | Establish canonical repository entrypoint     | Completed | Documentation accuracy                  |
| FF-04 | Reconcile architecture and claims             | Current   | Documentation accuracy                  |
| FF-05 | Build sanitized demo/evidence package         | Pending   | Demo reliability and evidence           |
| FF-06 | Run final verification and decide publication | Pending   | Verification and publishing preparation |

FF-04 completion does not authorize a push. FF-05 must not fabricate screenshots or evidence.
FF-06 must distinguish local verification from remote CI and production authorization.

## Deferred product/platform direction

The following are possible future work only:

- executable warehouse and dbt transformations;
- dimensional marts, Gold, reconciliation product, and dashboards;
- Portal operational adapters for Kafka, MinIO, Airflow, and Silver;
- production callback/abuse policy and security-runtime authorization;
- concrete external secret-provider/KMS integration;
- production identity and workload topology;
- production deployment, promotion, and rollback;
- full platform monitoring, alert delivery, and SLOs;
- backup/restore objectives, HA, multi-region, and DR;
- SBOM, signing, provenance, release attestation, and broader supply-chain controls.

No date or delivery commitment is attached to these items. Each would require a separate bounded
decision, implementation, verification, and claim review.

## Rejected during feature freeze

Until finalization is complete, the following are explicitly rejected:

- new business features or data products;
- schema redesign or new migration scope;
- architecture-layer expansion;
- new deployment targets or infrastructure-as-code;
- Portal data-plane adapters;
- discretionary dependency modernization;
- new scanner families or policy expansion unrelated to a critical correction;
- warehouse, dbt, Gold, dashboard, or catalog implementation;
- unrelated refactoring or cleanup.

Allowed post-freeze work is restricted to release/validation safety, critical security correction,
exception disposition, broken CI/test repair, documentation accuracy, demo reliability, evidence
reproducibility, and publishing preparation.

## Decision triggers for deferred work

A deferred item may enter a future roadmap only after it has:

1. a concrete business or operational requirement;
2. an authoritative owner and state boundary;
3. security and failure semantics;
4. compatibility and rollback analysis;
5. executable acceptance criteria;
6. an explicit claim boundary.

## Canonical navigation

- [Reviewer entrypoint](../README.md)
- [Current implemented architecture](architecture/current-state.md)
- [Target and optional architecture](architecture/target-architecture.md)
- [Canonical claims](architecture/claims.md)
- [Demo guide](demo/demo-guide.md)
