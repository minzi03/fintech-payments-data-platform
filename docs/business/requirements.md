# Business and Platform Requirements

## Requirement conventions

- `Must` is required for the target platform.
- `Should` is expected unless an ADR documents a trade-off.
- Target metrics are design goals, not measured results.

## Business requirements

| ID | Requirement | Priority | Acceptance evidence |
| --- | --- | --- | --- |
| BR-001 | Operations must view payment volume, success/failure rates, and latency by channel. | P0 | Governed operations mart and dashboard tests |
| BR-002 | Finance must reconcile daily partner settlement lines with internal transactions. | P0 | Classified reconciliation result for every eligible row |
| BR-003 | Users must drill down from aggregate KPIs to an auditable transaction or batch. | P0 | Traceable business and ingestion identifiers |
| BR-004 | Reprocessing must not create duplicate Silver or warehouse records. | P0 | Replay and idempotency integration tests |
| BR-005 | Sensitive customer and payment data must be access-controlled and masked by role. | P0 | Policy tests and access review evidence |
| BR-006 | Data owners must distinguish target metrics from observed metrics. | P0 | KPI catalog with measurement status |

## Functional requirements

| ID | Requirement |
| --- | --- |
| FR-001 | Ingest PostgreSQL changes through a CDC stream without losing delete or source-offset semantics. |
| FR-002 | Ingest versioned payment domain events independently of CDC records. |
| FR-003 | Ingest partner settlement files using checksums, manifests, quarantine, and replay-safe batch IDs. |
| FR-004 | Store immutable raw payloads and ingestion metadata in Bronze. |
| FR-005 | Validate contracts, normalize data types, deduplicate records, and isolate rejected data in Silver. |
| FR-006 | Build SCD Type 2 dimensions with non-overlapping validity intervals and one current version. |
| FR-007 | Build incremental facts with a late-arriving lookback and deterministic merge keys. |
| FR-008 | Produce payment-operations and reconciliation data products with documented grain. |
| FR-009 | Orchestrate batch dependencies and publish data only after quality checks pass. |
| FR-010 | Expose freshness, volume, quality, failure, and end-to-end latency signals. |

## Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-001 | Pipelines must be idempotent and recoverable by batch ID or source offset range. |
| NFR-002 | Currency values must retain declared decimal precision end to end. |
| NFR-003 | Data products must expose event time, ingestion time, and processing time where applicable. |
| NFR-004 | Failed validation must block publication without deleting the rejected evidence. |
| NFR-005 | Runtime components must emit structured logs and measurable health signals. |

## Data SLA targets

These are initial **target metrics** to validate in later phases:

| ID | Target | Validation method |
| --- | --- | --- |
| SLA-001 | Operational event data available within 2 minutes at the 95th percentile. | Event-time to serving-time benchmark |
| SLA-002 | Daily reconciliation published before 07:00 local business time. | Scheduled end-to-end run evidence |
| SLA-003 | Zero duplicate business keys after Silver deduplication. | Quality assertion and replay test |
| SLA-004 | Failed or quarantined files are never marked processed. | Manifest state-transition tests |

## Security requirements

| ID | Requirement |
| --- | --- |
| SEC-001 | Credentials must come from environment variables or an approved secret manager. |
| SEC-002 | Pipeline identities must use least-privilege roles and must not use administrative accounts. |
| SEC-003 | PII and payment-sensitive columns must be classified before business publication. |
| SEC-004 | Restricted columns must support masking or equivalent consumer-specific protection. |
| SEC-005 | Credentials and sensitive payloads must not appear in source control or application logs. |

## Out of scope for Phase 0

- Executable PostgreSQL schemas and synthetic business data.
- Kafka, Debezium, MinIO, Airflow, Spark, or Snowflake runtime services.
- Executable batch, CDC, streaming, Silver, or warehouse pipelines.
- Production dashboards, alerts, lineage, and access policies.
- Deployment automation or CD to a runtime environment.

## Phase 0 scope

Phase 0 delivers structure, documentation, local quality configuration, and CI. It does not satisfy the
runtime functional requirements above; later roadmap phases own their implementation and evidence.
