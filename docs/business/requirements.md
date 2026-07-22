# Business and Platform Requirements

## Conventions

- `Must` is required for the target platform; `Should` needs an ADR if omitted.
- `Implemented` means code exists through Phase 2; acceptance still depends on recorded test evidence.
- Target SLAs are design goals, not measured performance.

## Business requirements

| ID | Requirement | Phase 1 contribution |
| --- | --- | --- |
| BR-001 | Operations must view payment volume, success/failure rates, and latency by channel. | Source status, channel, and event-time data implemented; analytics planned |
| BR-002 | Finance must reconcile daily settlement lines with internal transactions. | Partner input is contract-validated and preserved; matching planned |
| BR-003 | Users must drill down to an auditable transaction or batch. | Transaction, event, trace, and partner identifiers implemented |
| BR-004 | Reprocessing must not create duplicate accepted records. | OLTP idempotency and settlement checksum replay protection implemented |
| BR-005 | Sensitive data must be access-controlled and minimized. | Generator omits national ID/card data; production policies planned |

## Phase 1 functional requirements

| ID | Requirement | Verification |
| --- | --- | --- |
| P1-FR-001 | Model customers, accounts, merchants, payments, immutable events, and refunds with declared grain. | Schema inspection and integration tests |
| P1-FR-002 | Store money as `NUMERIC(18,2)`/Python `Decimal`, never floating point. | SQL metadata and unit tests |
| P1-FR-003 | Enforce valid currencies, statuses, amounts, relationships, partner references, and idempotency keys. | Database constraint tests |
| P1-FR-004 | Produce successful, failed, pending-to-completed, pending-to-failed, event, and refund scenarios. | Deterministic generator unit tests |
| P1-FR-005 | Configure the generator by environment and CLI without source-controlled credentials. | Configuration/CLI tests and secret review |
| P1-FR-006 | Commit one generator run atomically and roll it back on failure. | Repository integration tests and connection handling |
| P1-FR-007 | Reject controlled invalid/duplicate probes without persisting bad data. | Savepoint-backed integration tests |

## Future functional requirements

| ID | Requirement | Planned phase |
| --- | --- | --- |
| FR-001 | Capture PostgreSQL changes with delete and source-offset semantics. | Phase 3 |
| FR-002 | Publish/ingest versioned payment events independently of CDC. | Phase 4 |
| FR-003 | Move partner transport and Bronze persistence from local filesystem to production storage. | Future hardening |
| FR-004 | Store immutable CDC/event raw payloads and metadata in Bronze. | Phases 3-4 |
| FR-005 | Normalize, deduplicate, validate, and quarantine in Silver. | Phase 5 |
| FR-006 | Build SCD Type 2 dimensions and incremental facts. | Phase 6 |
| FR-007 | Produce operations and reconciliation data products. | Phases 6-7 |
| FR-008 | Orchestrate dependencies and expose operational signals. | Phase 8 |

## Non-functional requirements

| ID | Requirement | Phase 1 status |
| --- | --- | --- |
| NFR-001 | Currency values retain declared decimal precision. | Implemented in schema and generator |
| NFR-002 | Source/event timestamps are timezone-aware UTC values. | Implemented and unit tested |
| NFR-003 | Generation is deterministic for the same seed and configuration. | Implemented and unit tested |
| NFR-004 | A failed generation iteration rolls back atomically. | Implemented with a database transaction |
| NFR-005 | Runtime code uses typed interfaces and actionable logs without secrets. | Implemented baseline |
| NFR-006 | Unit tests run without Docker; database tests are explicitly marked. | Implemented |
| NFR-007 | Production services are highly available, backed up, and observable. | Out of scope through Phase 2 |
| NFR-008 | A processed settlement checksum is not ingested again. | Implemented with SQLite manifest |
| NFR-009 | Bronze publication occurs before a manifest becomes `PROCESSED`. | Implemented and integration tested |
| NFR-010 | One invalid settlement record does not fail a file in partial-acceptance mode. | Implemented |

## Data SLA targets

The following are future targets and have not been measured in Phase 1:

| ID | Target | Planned validation |
| --- | --- | --- |
| SLA-001 | Operational event data available within 2 minutes at p95. | End-to-end streaming benchmark |
| SLA-002 | Daily reconciliation published before 07:00 local business time. | Scheduled end-to-end run evidence |
| SLA-003 | Zero duplicate business keys after Silver deduplication. | Replay and quality tests |
| SLA-004 | Failed files are never marked processed. | Batch manifest transition tests |

## Security requirements

| ID | Requirement | Phase 1 status |
| --- | --- | --- |
| SEC-001 | Credentials come from environment variables or an approved secret manager. | Environment variables implemented |
| SEC-002 | Credentials and full connection URLs do not appear in logs. | Safe connection label implemented |
| SEC-003 | Source control contains placeholders only, not real credentials. | Required review gate |
| SEC-004 | Unnecessary national IDs, card data, and bank credentials are not stored. | Implemented generator/data-model rule |
| SEC-005 | Production roles use least privilege, TLS, rotation, masking, and retention. | Planned hardening |

## Phase 2 functional requirements

| ID | Requirement | Verification |
| --- | --- | --- |
| P2-FR-001 | Enforce the versioned `settlement-v1` filename, schema, type, precision, status, and timestamp contract. | Contract/unit tests |
| P2-FR-002 | Calculate SHA-256 and persist deterministic file/control identity. | Manifest integration tests |
| P2-FR-003 | Preserve source bytes immutably in local partitioned Bronze. | Byte-equality integration test |
| P2-FR-004 | Separate file-level quarantine from row-level rejected-record evidence. | Batch integration tests |
| P2-FR-005 | Distinguish same-name/same-content, same-name/changed-content, and different-name/same-content discoveries. | Idempotency tests |
| P2-FR-006 | Never mark a file processed before required storage writes succeed. | Simulated Bronze failure test |
| P2-FR-007 | Generate deterministic candidate/error settlement scenarios. | Fixture tests |

## Out of scope for Phase 2

- Kafka, Debezium, MinIO, Airflow, Spark, Snowflake, executable dbt models, BI, and observability.
- CDC publication settings, event topics, Silver processing, reconciliation, and marts.
- Production deployment, HA, backup/restore automation, TLS, secret-manager integration, and load
  benchmarking.
- Cross-currency payment processing, overdrafts, chargebacks, disputes, and settlement matching.
- Partner SFTP/API transport, MinIO server integration, PGP, malware scanning, and distributed locks.
