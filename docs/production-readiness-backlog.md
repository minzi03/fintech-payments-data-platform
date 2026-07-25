# Production Readiness Backlog

- Version: `0.1-draft`
- Status: **DESIGN FREEZE IN PROGRESS**
- Last consolidated: 2026-07-24
- Production pilot: **BLOCKED**

## Purpose

This backlog closes the architecture-review phase and turns the consolidated findings into
implementation work. It is intentionally organized as production capabilities rather than as a
one-to-one list of review comments. A backlog item is complete only when its acceptance criteria
and required failure tests pass.

Effort uses `S` (up to three engineering days), `M` (up to two weeks), `L` (two to four weeks), and
`XL` (multi-team or longer than four weeks). Status values are `BACKLOG`, `DESIGNING`,
`DESIGN_FROZEN`, `IMPLEMENTING`, `VERIFYING`, `DONE`, and `BLOCKED`.

## Production blockers

The first controlled production pilot is blocked on these items, in this order:

1. `PRD-001` — Dataset bootstrap and atomic activation.
2. `PRD-002` — Key-only CDC delete semantics.
3. `PRD-003` — Cross-system recovery checkpoints.
4. `PRD-004` — Least-privilege runtime identities.
5. `PRD-005` — Versioned database migrations.

`PRD-001` is design-frozen by ADR-001 Revision 4 and
`docs/design-reviews/adr-001-design-freeze-review-v2.md`. Runtime implementation has not started.
The overall platform design freeze remains open until ADR-002 through ADR-005 pass their own
architecture reviews.

## Security and IAM

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-004 | P0 | Runtime services can be deployed with owner/admin-equivalent database or object-store identities. | PostgreSQL, MinIO/S3, Airflow, CDC consumer, Silver | Separate migration, runtime read, runtime write, control-plane, and recovery identities; deny-by-default bucket/database policies; secrets supplied by the deployment secret manager; local ports bind only to loopback; production deployment cannot start with root credentials. | Permission matrix tests; runtime attempts forbidden DDL/delete; credential rotation and service restart; production configuration rejection test. | L | None | BACKLOG |
| PRD-006 | P1 | Quarantine evidence can contain confidential raw records without a complete governance boundary. | Batch quarantine, CDC DLQ, MinIO/S3, operational tooling | Dedicated confidential namespace, encryption policy, restricted remediation role, retention and legal-hold rules, payload-free default inspection, and auditable access. | IAM denial tests; retention tests; inspection redaction tests; break-glass audit test. | M | PRD-004 | BACKLOG |
| PRD-007 | P1 | Audit-sensitive objects depend on application-level immutability but not storage-enforced retention. | Bronze, Silver history, publication manifests | Object versioning and retention lock where supported; delete denied to runtime identities; signed or tamper-evident publication manifests; administrative mutation is detectable. | Runtime overwrite/delete denial; version-preservation test; manifest signature verification; administrative tamper detection drill. | L | PRD-004, PRD-026 | BACKLOG |

## Disaster recovery

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-003 | P0 | PostgreSQL WAL/slot state, Kafka offsets, Bronze objects, Silver publications, and control metadata have no authoritative recovery checkpoint. | PostgreSQL, Debezium, Kafka, CDC manifest, object storage, Silver manifest | Durable per-partition checkpoint links committed next offset to verified Bronze ranges; active Silver versions reference verified inputs and outputs; restore reconciliation finds the first missing offset; recovery refuses to start when required Kafka data is no longer retained. | Crash after upload/before offset commit; manifest rollback with advanced Kafka offset; missing/corrupt object; stale slot; full restore drill from independent backups. | XL | PRD-001, PRD-002 | BACKLOG |
| PRD-008 | P1 | Recovery procedures are documented but not continuously proven. | All stateful services and runbooks | Scheduled restore drill in an isolated environment; measured RPO/RTO; evidence retained; failed drill blocks release promotion; recovery runbook is executable by an operator who did not implement the system. | Quarterly clean-room restore; prior-version restore; corrupt-backup rejection; operator handoff exercise. | L | PRD-003, PRD-005, PRD-024 | BACKLOG |
| PRD-009 | P1 | Local SQLite manifests are single-host state and cannot be failed over consistently. | Batch manifest, CDC manifest, Silver manifest | Move authoritative production manifests to a transactional HA database; enforce unique work identity, leases, fencing tokens, and monotonic state; retain SQLite only as an explicit local-development backend. | Concurrent claim; lease expiry; stale writer fencing; database failover during transition; retry after ambiguous commit. | XL | PRD-005 | BACKLOG |
| PRD-026 | P1 | Custom checksum metadata is not independent proof of long-term object integrity. | Bronze, Silver, quarantine, recovery tooling | Record client checksum, server-native checksum/version ID where available, object size and immutable manifest evidence; run periodic scrub and quarantine mismatches. | Post-upload verification; metadata-only tamper; byte corruption; restored-object scrub; sampled large-object verification. | L | PRD-004 | BACKLOG |

## CDC correctness

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-002 | P0 | Silver assumes PostgreSQL delete events contain a full pre-image although default replica identity can provide only the key. | PostgreSQL DDL, Debezium, CDC normalizer, Silver state/history | Key-only delete looks up prior state, creates an immutable deleted version, appends delete history, and removes CURRENT; missing prior state follows an explicit quarantine/full-pre-image fallback; full replica identity is enabled only where justified. | Real Debezium delete under default identity; missing prior state; tombstone absent/duplicate; restart between delete history and current publication. | L | PRD-001 | BACKLOG |
| PRD-010 | P0 | Kafka topic/partition/offset identity assumes a topic has one permanent transport incarnation. | Kafka, Bronze identity, Silver dedup, recovery | Source identity includes an immutable stream incarnation or cluster/topic ID; topic recreation cannot collide with historical events; restore and migration preserve the mapping. | Delete/recreate topic at offset zero; cluster migration; replay from restored Kafka; collision rejection. | L | PRD-003 | BACKLOG |
| PRD-011 | P0 | Silver assumes every update contains a complete row despite PostgreSQL TOAST/unavailable-value semantics. | Debezium configuration, Bronze contract, Silver projection | Partial updates are identified and merged against prior state without replacing values with placeholders; missing prior state is quarantined; schema documents unavailable-value semantics. | Large TOAST field unchanged update; changed TOAST field; no prior state; replay and force reprocess. | L | PRD-001 | BACKLOG |
| PRD-012 | P1 | Mutable reference/master data is outside the captured CDC set. | PostgreSQL reference tables, Debezium, Bronze, Silver, future warehouse | Currency, payment-channel, and merchant-category definitions are published as governed versioned data with effective dates; historical facts can be interpreted with the reference version valid at event time. | Reference rename/deactivation; new code; historical as-of join; replay with later reference version. | L | PRD-014 | BACKLOG |
| PRD-013 | P1 | Connector reconciliation cannot reliably prove secret/config convergence. | Kafka Connect API, PostgreSQL CDC role, connector bootstrap | Desired connector configuration has a non-secret fingerprint/version; masked secrets force safe reconciliation or an explicit rotation workflow; actual drift is visible and actionable. | Password rotation; manual REST drift; masked secret; create race; failed task diagnostics. | M | PRD-004, PRD-024 | BACKLOG |

## Snapshot activation

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-001 | P0 | Initial Debezium snapshot batches become active CURRENT before the source/table snapshot is complete. | Debezium notifications, Kafka, CDC consumer, Bronze control events, Silver manifest/storage/discovery | Frozen `payments-core-v1` group; one clean stream incarnation and attempt per generation; captured fence transaction and immutable connector-offset checkpoint; source-transaction-safe partition cut; lifecycle `INITIALIZING → SNAPSHOT_RUNNING → SNAPSHOT_CLOSED → CATCHING_UP → VALIDATING → READY`; immutable sealed candidate; separate atomic active pointer; fenced continuation and rollback; production HA state; staging excluded by IAM/catalog policy. | Partial snapshot invisibility; explicit-empty table; reused/non-empty stream namespace; second distinct snapshot start; source transaction spanning topics; post-fence change; crash in every state; duplicate/conflicting/missing notification; immutable candidate validation; competing activators; pointer failure; rollback with and without retained replay range; legacy-manifest migration; production backend and staging-policy validation. | XL | None | DESIGN_FROZEN |

## Schema migration

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-005 | P1 / pilot blocker | Init scripts bootstrap fresh databases but do not provide controlled upgrades. | OLTP schema, control DB, manifests | Versioned migration history with checksums; transactional grouping where PostgreSQL permits it; expand/migrate/contract workflow; migration locks; application/schema compatibility gates; rollback or forward-repair procedure. | Fresh install; upgrade from every supported release; failed migration rollback; checksum drift; two migrators; old/new application coexistence. | L | None | BACKLOG |
| PRD-014 | P1 | Only one active schema/code interpretation is assumed at a time. | Bronze, Silver, orchestration, future warehouse | Compatibility matrix supports parallel reader/writer versions; publication pointer is schema-scoped; breaking changes require new version; additive changes have explicit compatibility policy. | Old reader/new producer; new reader/old producer; parallel v1/v2 publication; rollback to prior version. | XL | PRD-001, PRD-005 | BACKLOG |
| PRD-028 | P1 | CURRENT is repeatedly materialized as a full immutable snapshot and will not scale with long-lived large entities. | Silver storage and future warehouse | Select an incremental table format or warehouse merge contract; state updates are bounded by changed keys; snapshot/version isolation and time travel remain explicit. | Hundred-million-row scale model; small update amplification; compaction; concurrent readers; rollback to prior snapshot. | XL | PRD-001, PRD-014 | BACKLOG |

## Orchestration and backfill

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-015 | P1 | Discovery and execution rescan mutable listings instead of processing a durably claimed object set. | Airflow DAGs, Silver discovery, manifests | Discovery creates immutable work items; workers claim with lease/fencing; claimed inputs equal processed and lineage inputs; workload limits apply to the DAG claim, not independently per task. | Concurrent schedulers; object arrives after discovery; lease expiry; worker crash; no starvation across entities. | L | PRD-009 | BACKLOG |
| PRD-016 | P1 | Backfill is a request record, not an authoritative workflow lifecycle. | Backfill DAG, control DB, Silver | Validated state machine with actor/source attribution, approval, claims, attempts, cancellation and supersession; overlapping scopes follow an explicit reject/queue/merge policy; request identity conflicts are rejected. | Overlapping backfills; same ID/different payload; cancellation; retry; actor attribution; dry-run write-free proof. | L | PRD-005, PRD-015 | BACKLOG |
| PRD-017 | P1 | Multi-step control-plane decisions are not an atomic, monotonic transaction. | ControlStore, pipeline/task/quality state | Transaction API records coupled changes atomically; terminal states are immutable except explicit recovery transitions; task retries create new tries; concurrent finalizers use optimistic locking/fencing. | Rollback between quality and completion; concurrent completion; same-try terminal conflict; retry try-number progression; transient database retry. | L | PRD-005 | BACKLOG |
| PRD-018 | P1 | Airflow metadata and platform control data share a resource/failure budget in the reference topology. | Airflow database, control database, connection pools | Independent connection budgets and SLOs; production topology supports a separate control database; control queries/vacuum cannot starve scheduler heartbeat or task scheduling. | Connection exhaustion; long control query; vacuum/load test; database failover; scheduler SLO under control workload. | M | PRD-004, PRD-005 | BACKLOG |
| PRD-027 | P1 | Batch completeness is inferred from files observed, not from expected partner deliveries. | Settlement ingestion, orchestration, control plane | Delivery calendar models partner/date/sequence expectations, cutoff/SLA, corrected files and waivers; a quiet inbound directory is distinguishable from complete settlement. | Missing file; late file; corrected file; holiday/calendar exception; duplicate delivery. | L | PRD-016 | BACKLOG |

## DLQ redrive

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-019 | P1 | Poison records are quarantined and their offsets committed, but there is no governed remediation/redrive lifecycle. | CDC consumer, quarantine, control DB, Bronze | Append-only lifecycle `QUARANTINED → TRIAGED → APPROVED → REDRIVING → RECOVERED/REJECTED`; preserve source coordinate/checksum; record parser and redrive-policy versions; successful redrive links exactly one Bronze result and cannot be repeated accidentally. | Parser regression batch; approval denial; crash during redrive; repeated redrive; new parser version; coordinate conflict. | L | PRD-004, PRD-009 | BACKLOG |

## Quality audit

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-020 | P1 | Re-evaluating the same rule overwrites the original control-plane evidence. | Control DB, quality rules, pipeline completion | Evaluations are append-only and include rule version, configuration hash, attempt and input dataset version; an immutable decision snapshot references the exact evaluations used to determine pipeline status. | Retry after threshold change; repeated identical evaluation; historical re-evaluation; decision reconstruction; concurrent evaluators. | M | PRD-005, PRD-017 | BACKLOG |
| PRD-021 | P1 | Aggregate rejection rate is insufficient evidence for financial data readiness. | Silver quality, orchestration gates, reconciliation | Versioned rule sets cover freshness, duplicates, gaps, schema compatibility, row-count anomaly and reference aging where applicable; each rule has an owner and blocking policy. | Boundary matrix; mixed PASS/WARN/FAIL; empty input; stale data; duplicate/gap; rule-version migration. | L | PRD-020, PRD-022 | BACKLOG |

## Observability

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-022 | P1 | Global freshness hides stalled Kafka partitions and PostgreSQL slot/WAL risk. | PostgreSQL, Kafka, Connect, CDC consumer, Bronze publisher | Separate liveness, progress, and data-freshness signals; per-partition committed/high-watermark lag and last publication; slot active state and retained WAL bytes; alert on backlog growth and no-progress duration. | One stalled partition; idle healthy source; negative/inconsistent offset; inactive slot; WAL growth; publication failure with live consumer. | L | PRD-003 | BACKLOG |
| PRD-023 | P2 | Generic `records_written` mixes business events, full-state materialization and physical output rows. | TaskResult, control DB, dashboards | Typed/versioned metrics expose input events, accepted events, state rows materialized, history rows appended, objects/bytes written and snapshot cardinality. | One event producing multiple outputs; large CURRENT rewrite; batch/CDC comparison; metric-version compatibility. | M | PRD-020 | BACKLOG |

## Release engineering

| ID | Severity | Finding | Affected components | Acceptance criteria | Required tests | Effort | Dependency | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRD-024 | P1 | A Git tag is not sufficient to reproduce and promote the exact runtime artifact. | Python dependencies, containers, CI/CD | Locked dependencies with hashes; base images pinned by digest; SBOM, vulnerability scan and artifact signature; the same immutable image digest is promoted and rolled back across environments. | Rebuild reproducibility; signature verification; dependency tamper; rollback by digest; SBOM policy gate. | L | None | BACKLOG |
| PRD-025 | P1 | Full-stack failure boundaries are not exercised at the correct release cadence. | CI, Compose/test environment, migrations, recovery | PR runs unit/contract/targeted integration and image builds; main/release runs clean PostgreSQL→Kafka→Bronze→Silver acceptance; nightly runs upgrade, crash and restore drills; flaky tests cannot be silently ignored. | Clean environment; previous-release upgrade; connector stopped during commit; upload-before-offset crash; restore drill. | L | PRD-005, PRD-008, PRD-024 | BACKLOG |
| PRD-029 | P2 | Production readiness evidence has no final governed sign-off package. | Architecture, backlog, ADRs, tests, pilot evidence | After all blocker ADRs merge and the pilot completes, publish `docs/production-readiness-report.md` with executive summary, final architecture, finding disposition, ADR links, proving tests, pilot results, accepted risks and remaining limitations. | Link/evidence validation; every finding has one terminal disposition; every fixed P0/P1 references an executable test; pilot evidence is attached. | M | PRD-001 through PRD-005, PRD-025 | BACKLOG |

## Delivery order

Each item uses the same vertical slice:

```text
ADR
→ schema/control changes
→ implementation
→ unit tests
→ integration tests
→ failure/recovery tests
→ runbook
→ focused sign-off
```

Recommended branches:

```text
feat/dataset-bootstrap-activation
feat/cdc-key-only-delete
feat/recovery-checkpoints
feat/runtime-least-privilege
feat/versioned-db-migrations
feat/dlq-redrive
feat/append-only-quality-evaluations
```
