# Final Design Freeze Review: ADR-001 Dataset Bootstrap and Atomic Activation

- Review date: 2026-07-24
- Reviewed revision: 4
- ADR: `docs/adr/001-dataset-bootstrap-and-atomic-activation.md`
- Review scope: Architecture contract only
- Verdict: **ACCEPTED**
- Design freeze: **GRANTED FOR ADR-001**

## Executive assessment

Revision 4 defines a complete architecture contract for the first verified bootstrap of
`payments-core-v1`. It replaces observation-time Kafka watermarks with a source-transaction fence,
seals an immutable candidate before validation, separates content readiness from serving
activation, and fails closed when required evidence cannot be established.

No unresolved P0 or P1 architecture flaw was found inside the declared scope.

Acceptance is deliberately narrow. It does not approve online re-snapshot, incremental snapshot,
cross-source merge, table-format migration, or the implementation. Those capabilities remain
outside ADR-001 and must not be inferred from this decision.

## Resolution of the first review

| First-review condition | Resolution in frozen Revision 4 |
| --- | --- |
| Source-consistent cut | Platform fence transaction, commit LSN, transaction metadata and immutable Kafka cut vector |
| Immutable validation target | Candidate is sealed before `VALIDATING`; post-cut records remain outside it |
| Attempt correlation | One platform generation, clean stream incarnation and snapshot attempt; notification identity is evidence only |
| Readiness versus activation | Terminal `READY` content plus separate append-only activation ledger and pointer |
| Publication scope | Frozen six-member serving group and separately frozen seven-collection connector capture set |
| Validation semantics | Transport completeness, record disposition and state correctness are separate decisions |
| Notification recovery | Immutable evidence, idempotent replay and fail-closed behavior |
| Legacy migration | `UNVERIFIED_LEGACY` cannot become active without reconciliation or clean bootstrap |
| Production concurrency | Transactional HA control store, locking, generations and fencing tokens are mandatory |
| Staging isolation | IAM/catalog policy prevents production discovery; serving uses exact active-manifest references |

## Final architecture challenges

### Snapshot row-to-attempt identity

Debezium row envelopes do not provide the platform's `bootstrap_attempt_id`. Notification
correlation alone therefore cannot prevent row records from two snapshot attempts in the same
topics from entering one candidate.

Revision 4 closes this ambiguity by allowing exactly one attempt in an immutable, initially empty
stream incarnation. A distinct later `STARTED` invalidates the generation and requires a new
connector, slot and topic incarnation. Topic coordinates can consequently identify attempt inputs
without relying on wall-clock correlation.

The logical incarnation evidence is retained, but a terminal failed generation does not leave a
physical replication slot retaining WAL indefinitely. Its connector and slot follow an audited
retirement workflow and their identities cannot be reused.

### Source fence capture

The fence is now a first-class auxiliary captured collection rather than an implied control
record. The connector publication, include list and capture-set hash must all include
`payments.cdc_bootstrap_fences`, while the serving publication group excludes it.

This resolves the earlier ambiguity in which the architecture depended on a fence event without
freezing how that event entered the same CDC stream.

### Connector offset evidence

Observing the fence event in Kafka alone does not prove that the connector has durably advanced
its source offset. Revision 4 requires an immutable connector-offset checkpoint at or beyond the
fence LSN. Candidate closure is forbidden when that checkpoint is missing, ambiguous or
unverifiable.

This makes the cut recoverable and auditable rather than dependent on a transient health reading.

### Rollback and continuation

Serving rollback can no longer change only the active pointer while a stream worker continues from
a newer cursor. Rollback fences the continuation epoch, binds the target version to its successor
cursor and requires the full replay range to remain checksum-verifiable in Bronze.

This preserves consistency between the version being served and the event position from which
processing resumes.

### Snapshot count evidence

Initial activation now requires authoritative scan-count evidence for every non-empty member.
Missing or malformed count evidence is blocking. Explicit-empty scan evidence remains valid and
does not rely on the presence of a row event.

## Frozen invariants

The following invariants are architecture contracts for implementation:

1. A dataset version represents one completed PostgreSQL transaction cut.
2. No source transaction is split across the candidate boundary.
3. The six serving members activate as one publication group.
4. The auxiliary fence collection is captured but never served.
5. A generation has one clean stream incarnation and exactly one snapshot attempt.
6. A candidate is immutable before validation begins.
7. `READY` does not imply active; activation requires one atomic pointer transaction.
8. Only a `READY` version with a successful immutable validation decision can become active.
9. No staging or unverified legacy object is discoverable through the production serving path.
10. Missing, conflicting, reordered or expired evidence fails the attempt closed.
11. Post-cut records remain durable and resume only from the recorded successor vector.
12. Rollback changes the active version and continuation epoch as one coordinated operation.
13. A stale worker cannot seal, validate, activate, publish or advance continuation progress.
14. Production state uses a transactional HA store; SQLite is local-only.
15. Direct prefix scans are not a supported production read path.

## Required proof before feature merge

Design acceptance is not implementation acceptance. The vertical slice must prove:

- complete and explicit-empty initial snapshots;
- a multi-table source transaction cannot be split by the cut;
- events committed after the fence are excluded from the candidate and replayed after activation;
- restart in every non-terminal state converges idempotently;
- duplicate and conflicting notifications behave according to the frozen contract;
- a second distinct `STARTED` cannot mix its rows with the first attempt;
- a reused or non-empty stream namespace is rejected before connector start;
- terminal failed generations retire their connector/slot without deleting forensic evidence;
- a candidate cannot change during validation;
- two workers cannot activate competing versions;
- partial output writes are invisible to serving readers;
- rollback fences old workers and resumes at the target successor vector;
- rollback is rejected when the required replay range is unavailable;
- production configuration rejects SQLite and staging-readable serving identities;
- legacy outputs cannot become active through migration alone.

## Decision

**ADR-001 is accepted and frozen at Revision 4.**

Any implementation change that alters the source-cut definition, publication membership, state
machine, validation layers, activation transaction, continuation epoch, rollback preconditions or
staging visibility requires an explicit ADR amendment and a new design review.
