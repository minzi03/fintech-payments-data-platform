# Design Freeze Review: ADR-001 Dataset Bootstrap and Atomic Activation

- Review date: 2026-07-24
- Reviewed revision: 1
- ADR: `docs/adr/001-dataset-bootstrap-and-atomic-activation.md`
- Review scope: Architecture contract only
- Verdict: **REVISION REQUIRED**
- Design freeze: **NOT GRANTED**

## Executive assessment

The central pattern is correct:

```text
immutable staging
→ completeness validation
→ atomic active pointer
```

However, ADR-001 does not yet define an authoritative source-consistent activation cut. It also
allows the candidate version to keep changing while it is being validated and conflates version
readiness with serving activation. These gaps can still expose a transactionally inconsistent or
unreproducible `CURRENT` dataset even if every state transition is implemented exactly as written.

The ADR should be revised rather than rejected. Its publication model is viable once the following
architectural flaws and invariants are resolved.

## Remaining architectural flaws

### DF-001 — P0: Kafka end offsets observed after `COMPLETED` are not a source-consistent cut

ADR-001 defines catch-up targets by reading every table-topic end offset when the notification
consumer observes `COMPLETED`.

Kafka provides ordering within a partition, not an atomic order across the notification topic and
all captured table-topic partitions. The observed vector can therefore:

- omit snapshot or streaming records still in flight to another topic;
- include arbitrary later streaming records;
- cut through one PostgreSQL transaction whose table changes are spread across topics.

Activation at such a vector can publish a cross-table state that never existed in PostgreSQL.

**Required invariant:** the publication cut must identify one completed source transaction boundary.
Every captured change at or before that boundary must be included, and every later change must be
excluded from that immutable candidate and preserved for subsequent application.

### DF-002 — P0: The candidate remains mutable during validation and activation

The ADR says streaming changes merge into the candidate while it is open, but it does not define
when the candidate stops accepting changes.

Validation can therefore inspect output set A while a worker publishes output set B. The active
pointer might reference checksums that were valid before a later merge, or post-validation events
might be omitted without an explicit handoff.

**Required invariant:** entering validation freezes an immutable publication cut and output set.
Events beyond that cut must be durably retained outside the frozen candidate and applied only after
activation through an ordered continuation.

### DF-003 — P0: Snapshot notification identity is not a correlation identity

The dataset-version identity includes a "Debezium snapshot notification identity." Debezium defines
the notification `id` as an identifier assigned to a notification; only some snapshot types reuse a
caller-supplied signal ID. ADR-001 does not establish that `STARTED`, table-scan, `COMPLETED`,
`ABORTED`, duplicate, and restart notifications share one stable initial-snapshot identifier.

Multiple snapshot attempts from the same connector can therefore be associated with the same open
candidate or one attempt can be split across multiple candidates.

**Required invariant:** the platform owns one durable bootstrap-generation identity and defines an
unambiguous rule for associating every notification and table record with that generation.
Connector restart, duplicate `STARTED`, `ABORTED`, `SKIPPED`, and a later new snapshot must not
reuse or merge generations.

Debezium notification identity and lifecycle semantics are documented in the
[official Debezium notification reference](https://debezium.io/documentation/reference/configuration/notification.html).

### DF-004 — P1: Version readiness and serving activation are one mutable state machine

`ACTIVE → SUPERSEDED` treats serving status as a mutable property of dataset content. This leaves
rollback semantics undefined:

- Does a superseded version transition back to `ACTIVE`?
- Is rollback a new generation?
- Can one validated version be activated more than once?
- Which transition is the immutable audit record?

The current model also has no explicit `READY` state between validation and activation.

**Required invariant:** immutable version readiness and mutable serving selection are separate
concepts. A version reaches terminal `READY`, `FAILED`, `ABORTED`, or `CANCELLED`; an append-only
activation event changes the active pointer by generation. Rollback is another activation event,
not a reverse mutation of the version.

### DF-005 — P1: The publication group and captured-table membership are ambiguous

The ADR alternates between state "per source/table" and a version scoped to a captured table set.
It does not state whether activation is atomic:

- per table;
- for all six captured tables;
- for a dependency group;
- or for the whole source connector.

It also does not freeze the exact table list and connector configuration for the duration of the
snapshot. Adding or removing a table during bootstrap can change the completeness requirement
while the candidate is running.

**Required invariant:** every version carries an immutable publication-group ID, exact ordered table
membership, table-set/configuration hash, expected output type per member, and schema compatibility
contract. Membership cannot change after `INITIALIZING`.

### DF-006 — P1: Row-count validation mixes transport, quality, and state cardinality

The ADR requires snapshot row reconciliation but does not define which counts must agree. These are
different quantities:

```text
source rows scanned
Bronze snapshot records
Silver accepted records
Silver rejected records
unique latest keys
CURRENT rows after catch-up updates/deletes
```

Catch-up changes legitimately make CURRENT cardinality differ from the source snapshot count. A
quality rejection can make the final state incomplete even though the transport is complete.

**Required invariant:** validate the layers separately:

1. Source scan evidence equals durable snapshot transport evidence.
2. Every Bronze record has exactly one accepted/rejected disposition.
3. Initial activation defines whether any rejection is allowed; the safe default is zero unresolved
   blocking rejection.
4. State cardinality is reconciled at the frozen source cut, not compared directly with raw scan
   count.

### DF-007 — P1: Missing and reordered notifications have no recovery contract

The ADR makes notifications part of the correctness boundary but specifies only monitoring and
retention. It does not define the state outcome when:

- `STARTED` is missing but table-scan evidence arrives;
- one table-scan notification is missing;
- `COMPLETED` is duplicated or arrives after restart;
- notification retention expires;
- `SKIPPED` is emitted;
- a table scan reports an unsupported or unknown status.

**Required invariant:** notification evidence is append-only and reconciled against an authoritative
snapshot attempt. Ambiguous or incomplete evidence must fail closed. Manual recovery must add
audited evidence; it must not mutate or invent the original notification.

### DF-008 — P1: The first activation and legacy migration cannot both be fail-closed

The migration plan imports existing `latest_output()` selections as an active `LEGACY_IMPORT`
version after checking objects and output presence. Those checks prove artifact integrity, not
source completeness. The imported output could be the same partial snapshot that ADR-001 is meant
to prevent.

**Required invariant:** an existing output cannot become a verified active baseline without source
completeness evidence. The architecture must choose one of:

- remain explicitly `BOOTSTRAPPING` until a verified snapshot activates;
- reconcile a legacy baseline against the source at a defined cut;
- expose it only as `UNVERIFIED` under an explicit accepted-risk decision.

No legacy fallback may be silently labeled `ACTIVE`.

### DF-009 — P1: Correctness-critical state durability is deferred to another backlog item

ADR-001 says dataset-state storage is correctness-critical but postpones HA state to `PRD-009`.
Atomic activation is only as strong as the store holding the candidate, validation decision,
generation and active pointer.

**Required invariant:** the production ADR must require a transactional, fenced, recoverable control
store as part of the activation contract. A SQLite implementation may remain a local adapter, but
it cannot satisfy production acceptance for PRD-001.

### DF-010 — P1: “Unsupported direct prefix scan” does not prevent partial reads

The original corruption path remains possible if a query engine, operator, or later warehouse
loader can list the staging prefix directly. Declaring the active resolver to be the supported API
is not an isolation boundary.

**Required invariant:** production reader identities and catalogs cannot discover staging objects
as active datasets. All serving integrations resolve one signed/immutable activation manifest, and
staging access is limited to publisher and recovery identities.

## Missing state-machine contract

The ADR should separate two state machines.

### Dataset-version build state

```text
INITIALIZING
    ↓
SNAPSHOT_RUNNING
    ↓
SNAPSHOT_CLOSED
    ↓
CATCHING_UP
    ↓
VALIDATING
    ↓
READY

INITIALIZING | SNAPSHOT_RUNNING | SNAPSHOT_CLOSED | CATCHING_UP | VALIDATING
    → FAILED | ABORTED | CANCELLED
```

`SKIPPED` is notification evidence, not automatically a successful version. With no verified active
version it leaves the dataset unavailable; with an existing active version it creates no new
candidate.

### Serving activation state

```text
active pointer generation N
    ↓ compare-and-swap using one READY version and immutable cut vector
active pointer generation N+1
```

Every activation or rollback creates an append-only event containing:

- prior and next version IDs;
- publication-group and table-set hash;
- source transaction cut and per-partition coordinates;
- complete output manifest and checksums;
- validation decision ID;
- actor/reason;
- pointer generation and timestamp.

Dataset-version content does not transition backward when rollback occurs.

## Design-freeze conditions

ADR-001 can move to `Accepted` only when it explicitly defines:

1. An authoritative source-transaction cut that cannot split a transaction.
2. Candidate freezing and the ordered handling of post-cut events.
3. Platform-owned bootstrap-generation correlation.
4. Separate version-readiness and activation state machines.
5. Immutable publication-group membership and configuration hash.
6. Layered count/quality/state validation with a blocking-rejection policy.
7. Missing, duplicate, reordered, skipped and aborted notification behavior.
8. A fail-closed first-activation and legacy migration policy.
9. A production-grade authoritative state-store requirement.
10. Enforced staging isolation for every production reader.

## Verdict

**REVISED REQUIRED.**

ADR-001 retains the correct top-level publication pattern, so rejection is not warranted. Design
freeze is withheld because DF-001 through DF-003 can still produce an inconsistent active dataset,
and the remaining invariants are necessary for deterministic rollback and recovery.
