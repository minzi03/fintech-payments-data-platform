# ADR-001: Dataset Bootstrap and Atomic Activation

- Status: Accepted, Frozen Revision 4
- Date: 2026-07-24
- Backlog: `PRD-001`
- Design review v1: `docs/design-reviews/adr-001-design-freeze-review-v1.md`
- Final design review: `docs/design-reviews/adr-001-design-freeze-review-v2.md`

## Context

Debezium initial snapshots emit table records with operation `r`, followed by WAL streaming
records. Completion of one Bronze object proves neither that the source snapshot is complete nor
that all changes through one source transaction have reached Silver.

The publication contract must establish:

- one immutable, source-consistent dataset version;
- a clear boundary between events included in that version and later events;
- an atomic serving pointer;
- deterministic notification, replay, restart and rollback behavior.

Per-record snapshot flags and Kafka end offsets observed at wall-clock time are evidence, but they
are not a source-consistent publication cut.

## Scope

This ADR governs the first verified bootstrap of the `payments-core` PostgreSQL CDC publication
group. It also governs restart of that same bootstrap attempt before activation.

Online re-snapshot of an already active dataset, incremental/ad-hoc snapshots, cross-source merge,
and table-format replacement are separate decisions. If an active dataset unexpectedly receives a
new snapshot outside an approved refresh design, the active version remains unchanged and the new
snapshot is failed closed.

## Terms and immutable identities

### Publication group

`payments-core-v1` is one atomic group containing:

```text
customers
accounts
merchants
payment_transactions
transaction_events
refunds
```

Each dataset version stores the exact ordered serving membership, required output type per member,
source database identity, connector configuration hash, captured-table-set hash, Bronze schema
version and Silver schema version. Membership and hashes cannot change after initialization.

The required state outputs are:

- `LATEST_ALL` and `CURRENT` for state entities;
- `EVENTS` for `transaction_events`;
- `HISTORY` for every member with accepted CDC records.

### Capture set and fence collection

The connector capture set is larger than the serving publication group. It contains the six
serving members plus one platform-owned auxiliary collection:

```text
payments.cdc_bootstrap_fences
```

The fence collection is included explicitly in the PostgreSQL publication, connector include
list and captured-table-set hash. It is excluded from Silver serving membership and state
cardinality. Only the bootstrap coordinator identity can insert a fence; application and serving
identities have no write permission.

Each fence row has a unique generation/attempt identity and an unpredictable nonce. Its Debezium
record supplies the authoritative source transaction identity and commit LSN used by the source
cut. A fence record from any other collection or identity cannot close a candidate.

### Bootstrap generation and attempt

The platform creates a durable `bootstrap_generation_id` before connector registration. It owns
this identity; a Debezium notification ID is never used as the generation identity.

Each generation owns one immutable stream incarnation:

```text
connector identity
+ replication slot identity
+ table-topic prefix
+ notification-topic identity
+ transaction-topic identity
```

The table-topic namespace must be new and empty before the connector is allowed to start. Its
incarnation identity and generation-start partition vector are persisted before connector
registration. Reusing a historical topic namespace, connector identity or slot for a new
generation is rejected.

One generation permits exactly one snapshot attempt. The first valid `STARTED` notification
creates its `bootstrap_attempt_id`. Duplicate delivery of that same notification coordinate and
checksum is idempotent. A second distinct `STARTED` invalidates the generation; it cannot open a
new attempt in the same stream incarnation. Retrying a failed or restarted snapshot requires a new
generation and a new stream incarnation.

Snapshot and streaming row records belong to the attempt through the unique connector/topic
incarnation plus their immutable Kafka coordinates. No record or notification can belong to two
generations or attempts.

Notification ID, Kafka coordinate and content checksum are immutable evidence. Same coordinate and
same checksum is idempotent; same coordinate and different checksum is a hard conflict.

### Source cut

The immutable dataset version is bounded by:

```text
source fence transaction ID
+ source fence commit LSN
+ per-topic-partition Kafka cut vector
```

The cut is a completed PostgreSQL transaction, not a wall-clock timestamp and not an arbitrary
Kafka high watermark.

## Decision

### Ordered snapshot evidence

Debezium sink notifications are written to an explicit topic keyed by connector identity. The
production notification contract requires one ordered partition per connector key and retention
longer than the maximum bootstrap and recovery window.

Every notification is persisted as immutable Bronze control evidence before its Kafka offset is
committed.

Notification meaning:

- `STARTED` opens a new attempt.
- Successful or explicit-empty `TABLE_SCAN_COMPLETED` closes one member scan.
- Global `COMPLETED` closes snapshot scanning but does not make the dataset ready.
- `ABORTED`, failed/unknown table status, irreconcilable `SKIPPED`, missing evidence or retention
  loss closes the attempt without a candidate.

`SKIPPED` creates no new dataset version. If no verified active version exists, the dataset remains
unavailable with status `BOOTSTRAP_REQUIRED`. If an active version exists, it remains active.

### Source-consistent fence

After successful global snapshot completion, the coordinator commits a platform-owned fence
transaction to `payments.cdc_bootstrap_fences`. The fence includes generation, attempt and nonce.

Candidate closure requires both:

1. the exact fence event has been published and persisted as immutable Bronze control evidence;
2. an immutable connector-offset checkpoint proves the connector durably committed a source
   offset at or beyond the fence commit LSN.

The offset checkpoint records connector identity, connector configuration hash, source offset,
fence event coordinate, fence transaction/LSN, observation method, checksum and observation time.
It is evidence, not a mutable health sample. If authoritative committed-offset evidence is
unavailable, ambiguous or behind the fence, the attempt cannot close.

After this checkpoint, no later connector delivery may introduce an event whose source transaction
commits at or before the fence LSN. A violation is a hard ordering conflict and invalidates the
candidate.

The candidate Kafka cut vector is derived from the highest Kafka coordinate in each captured
topic-partition whose completed source transaction is at or before the fence. It is not derived
from the topic end offset. Debezium transaction metadata proves that no transaction at or before
the fence is partially represented.

Within each captured topic-partition, completed source transaction LSNs must be non-decreasing.
The validator rejects an inversion rather than guessing an order. Kafka offsets are used only
inside one topic-partition; they are never compared globally.

Partitions with no qualifying record carry an explicit empty cut anchored to their generation
start position. The complete vector is immutable once recorded.

### Version build state

Dataset content has a monotonic build lifecycle:

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

`READY`, `FAILED`, `ABORTED` and `CANCELLED` are terminal content states. Serving status is not a
dataset-version state.

### Staging and the frozen candidate

Snapshot and pre-cut streaming events build immutable staging outputs under the generation and
attempt identity. `CATCHING_UP` accepts only events whose completed source transaction is at or
before the source fence and whose coordinate does not exceed the partition cut.

When every partition reaches its cut, the coordinator seals one immutable candidate manifest.
Sealing records the complete input set, output set, checksums, transaction cut, partition vector,
table-set hash and schema versions. After sealing:

- the candidate accepts no further input or output;
- `VALIDATING` reads only the sealed manifest;
- events beyond the source cut remain durably ordered in Bronze and are not part of the candidate;
- post-cut processing starts from the successor of the cut only after activation.

There is no interval in which validation observes a moving output set.

### Layered validation

Validation produces an immutable decision and evaluates separate invariants.

#### Transport completeness

- Every configured member has successful or explicit-empty scan evidence.
- Every non-empty member has a persisted authoritative scan count. Missing, malformed or
  unavailable count evidence blocks initial activation.
- Durable Bronze snapshot coordinates reconcile with source scan evidence.
- Every source transaction at or before the fence is complete.
- Every partition has reached its immutable cut without a gap or content conflict.

#### Record disposition

For every Bronze input record:

```text
accepted + rejected = input
```

Initial activation permits no unresolved blocking rejection. A non-blocking warning must be named
by the frozen rule-set version and referenced by the validation decision.

#### State correctness

State cardinality is derived from the snapshot key set plus accepted create/delete changes through
the source fence. It is not compared directly with raw scan count after catch-up. Business-key
uniqueness, required outputs and referential validation are evaluated at the same frozen cut.

Only a successful immutable validation decision moves a version to `READY`.

### Activation and rollback

Activation is separate from version build state. It appends an activation event and updates one
active pointer using compare-and-swap in the same transaction.

The activation event contains:

- previous and next version IDs;
- publication group and configuration hashes;
- source fence transaction/LSN and partition cut vector;
- sealed input/output manifest and checksums;
- schema versions and validation decision ID;
- actor, reason, pointer generation and timestamp.

The pointer can reference only `READY` content. A failed transaction leaves the prior pointer
unchanged.

Rollback is a coordinated activation, not a pointer-only edit. Before rollback, normal stream
application is paused at a durable continuation checkpoint and its lease epoch is fenced. The
rollback event binds the retained `READY` version to that version's successor cursor vector and a
new continuation epoch.

Rollback is allowed only when every event from the target successor vector to the desired resume
point remains available and checksum-verifiable in Bronze. After the pointer transaction commits,
stream application replays from the target successor vector under the new epoch. If the required
range is unavailable, rollback is rejected and recovery follows ADR-003.

Rollback does not mutate an old version backward from a superseded state. Activation and
continuation history are append-only. A worker holding an older continuation epoch cannot publish
or advance progress after activation or rollback.

If no verified active pointer exists, the serving contract returns `BOOTSTRAPPING` or
`UNAVAILABLE`; it never falls back to a staging or unverified output.

### Authoritative state and concurrency

Production bootstrap state, sealed manifests, validation decisions, activation events and the
active pointer reside in a transactional HA control store that supports:

- unique generation and attempt constraints;
- row/advisory locking;
- optimistic generations and fencing tokens;
- atomic compare-and-swap activation;
- backup, point-in-time recovery and failover.

SQLite may implement the same interface for local development, but it cannot satisfy production
acceptance for this ADR.

Only one open attempt is allowed per publication group and generation. A stale worker cannot seal,
validate or activate after its fencing token expires.

### Notification recovery

Notification evidence is append-only. Missing, reordered, conflicting or expired evidence fails
the attempt closed.

Restart resumes the persisted generation/attempt state and replays evidence idempotently. Manual
recovery may append an approved recovery decision linked to independently verified connector and
source evidence; it cannot edit or synthesize the original notification.

### Staging isolation

Production readers and catalogs cannot list or discover staging objects. Staging list/read access
is limited to publisher and recovery identities.

Serving integrations receive exact object references only from the active activation manifest.
Direct prefix scanning is not merely unsupported; it is excluded by IAM/catalog policy and is a
failed deployment validation if detected.

## Alternatives considered

### Kafka end offsets observed after snapshot notification

Rejected because topics and partitions do not share an atomic order and the resulting vector can
split a source transaction.

### Activate on the final snapshot row

Rejected because it does not prove empty-table completion, complete table membership, or Silver
catch-up.

### Keep merging while validation runs

Rejected because validation and activation would refer to different candidate contents.

### Store serving status on the dataset version

Rejected because rollback would require reversing terminal version state. An activation ledger and
pointer preserve immutable content state.

### Copy objects to an active prefix

Rejected because multi-object copy is not an atomic serving boundary. The activation manifest is
the boundary.

## Consequences

- Bootstrap requires a captured source fence and Debezium transaction metadata.
- Every bootstrap retry requires a new immutable stream incarnation. After terminal failure, the
  connector is stopped and its physical replication slot is retired through an audited cleanup
  workflow so it cannot retain WAL indefinitely. Topic, object, notification, offset and logical
  slot-identity evidence remain retained; no retired identity is reused.
- Notification evidence is necessary for lifecycle progress but is not the source cut.
- Post-cut events can wait while a candidate is validated; lag is explicit and bounded by
  operational SLOs.
- Production requires an HA control store before PRD-001 can be accepted as implemented.
- All readers must integrate with the active activation manifest.
- The whole `payments-core-v1` group activates together; one failed member blocks the version.
- Online refresh of an already active dataset remains out of scope and fails closed.

## Migration and backward compatibility

Existing completed Silver outputs are imported only as `UNVERIFIED_LEGACY` evidence. Object and
checksum validation does not promote them to `READY` or make them active.

Production remains `BOOTSTRAP_REQUIRED` until one of the following occurs:

1. A clean verified bootstrap completes under this ADR.
2. An explicit source reconciliation establishes an equivalent transaction cut, sealed manifest
   and validation decision for the legacy outputs.

Local demonstration mode may opt into legacy reads with an explicit non-production flag. That flag
is rejected by production configuration validation and never creates an active pointer.

Schema rollout is expand-first:

1. Add generation, attempt, version, cut, validation, activation and pointer structures.
2. Deploy evidence ingestion while legacy reads continue only in local environments.
3. Complete one clean bootstrap.
4. Activate the verified version atomically.
5. Enable active-manifest resolution for all readers.
6. Remove legacy-read capability from production artifacts.

## Operational impact

Operators can observe generation, attempt, table-scan evidence, source fence, per-partition cut
progress, sealed candidate, validation decision, active pointer generation and post-cut lag.

Alerts cover:

- missing or conflicting notification evidence;
- fence transaction not captured or source offset not committed;
- missing or unauditable connector-offset checkpoint;
- incomplete source transaction;
- source commit-LSN inversion within a Kafka partition;
- terminal failed generation whose connector or replication slot was not retired;
- partition gap or stalled catch-up;
- seal or validation timeout;
- stale fencing token;
- activation compare-and-swap conflict;
- staging access-policy drift;
- no verified active version.

## Design-freeze acceptance conditions

Design freeze requires an architecture-only review to confirm:

1. The fence and cut cannot split a source transaction.
2. The candidate is immutable before validation.
3. Generation and attempt correlation is unambiguous.
4. Version readiness and serving activation are separate.
5. Publication membership and configuration are immutable.
6. Transport, disposition and state validation are distinct.
7. Missing/duplicate/reordered/skipped notification behavior fails closed.
8. First activation and legacy migration cannot expose unverified data.
9. The production state store satisfies atomic activation and fencing.
10. Staging output cannot be discovered by production readers.
11. The auxiliary fence collection is part of the frozen connector capture contract.
12. Rollback changes serving and continuation epochs together and requires retained replay data.
13. Snapshot rows from different attempts cannot share a stream incarnation or candidate.
