# Platform Design Freeze

- Freeze phase started: 2026-07-24
- Overall phase status: **IN PROGRESS**
- Production-remediation implementation status: **NOT STARTED**
- Governing backlog: `docs/production-readiness-backlog.md`
- Active bounded exception: `GD-001` for PR-PORTAL-002 only

## Purpose

This phase converts the completed architecture review into enforceable design contracts before
runtime implementation begins. It does not approve the repository for a production pilot or
production deployment.

Design freeze is applied one blocker at a time. An accepted ADR freezes only its declared scope;
it does not implicitly approve another feature or the current implementation.

Corrective maintenance that does not implement or alter a frozen blocker contract may continue,
but it must pass the existing Phase 7 verification gates.

## Global exit criteria

The platform exits Design Freeze only when:

- ADR-001 through ADR-005 are accepted by architecture-only reviews;
- Production Readiness Backlog v1.0 is frozen;
- affected data models are enumerated and frozen;
- all feature state machines are explicit and monotonic;
- manifest identities, evidence and lifecycle contracts are frozen;
- publication and serving boundaries are frozen;
- dependencies and migration ordering across the five blockers are consistent;
- each blocker has implementation acceptance criteria and required failure/recovery tests;
- no runtime implementation was started against a proposed or revised-required ADR.

## Status

| Blocker                                         | ADR                | Design review                                            | Freeze status         | Implementation     |
| ----------------------------------------------- | ------------------ | -------------------------------------------------------- | --------------------- | ------------------ |
| PRD-001 Dataset bootstrap and atomic activation | ADR-001 Revision 4 | `docs/design-reviews/adr-001-design-freeze-review-v2.md` | **ACCEPTED / FROZEN** | Gated, not started |
| PRD-002 CDC key-only delete semantics           | ADR-002            | Not started                                              | Proposed              | Blocked            |
| PRD-003 Cross-system recovery checkpoints       | ADR-003            | Not started                                              | Proposed              | Blocked            |
| PRD-004 Runtime identity and storage IAM        | ADR-004            | Not started                                              | Proposed              | Blocked            |
| PRD-005 Versioned database migrations           | ADR-005            | Not started                                              | Proposed              | Blocked            |

The overall Design Freeze remains open. ADR-001 acceptance is not a platform-wide freeze.

## Active bounded implementation exception

[`GD-001`](governance/decisions/GD-001-portal-002-implementation-exception.md) authorizes
PR-PORTAL-002 implementation with conditions after the decision is merged into the protected
default branch.

GD-001:

- does not complete this repository-wide Design Freeze;
- does not alter ADR-001 through ADR-005 status or scope;
- does not authorize any platform production-remediation implementation;
- does not authorize Source, CDC, Dataset, Pipeline, Recovery, or other domain APIs;
- expires when PR-PORTAL-002 merges, is abandoned/revoked, reopens a frozen Portal security ADR,
  or reaches its recorded expiry date;
- grants no production deployment authority.

This is the sole current exception to the default implementation prohibition.

ADR-001 frozen artifact:

```text
revision: 4
sha256: 2f8267d92704b6f7bfef841c50b0c1bd1653d6e9e8120a23f24efbbff6e24768
```

## ADR-001 frozen contract

### Frozen data model

The implementation must represent the following durable concepts without collapsing their
identities:

```text
publication_group
bootstrap_generation
stream_incarnation
bootstrap_attempt
snapshot_notification_evidence
source_fence
connector_offset_checkpoint
partition_cut
dataset_version
sealed_candidate_manifest
validation_decision
activation_event
active_publication_pointer
continuation_checkpoint
continuation_epoch
```

Production instances of these records require transactional HA persistence, uniqueness,
optimistic generations, fencing and point-in-time recovery. SQLite remains a local-development
adapter only.

### Frozen build state machine

```text
INITIALIZING
    |
SNAPSHOT_RUNNING
    |
SNAPSHOT_CLOSED
    |
CATCHING_UP
    |
VALIDATING
    |
READY

INITIALIZING | SNAPSHOT_RUNNING | SNAPSHOT_CLOSED | CATCHING_UP | VALIDATING
    -> FAILED | ABORTED | CANCELLED
```

`READY`, `FAILED`, `ABORTED` and `CANCELLED` are terminal content states. Serving activation is a
separate append-only event and active pointer. A future implementation must not add `ACTIVE` or
`SUPERSEDED` as mutable dataset-version states without amending ADR-001.

### Frozen manifest contract

A sealed candidate manifest is immutable and contains:

- publication group and exact serving membership;
- exact connector capture set, including the auxiliary fence collection;
- generation, attempt and version identities;
- connector, table-set and configuration hashes;
- complete immutable input and output object references and checksums;
- source fence transaction ID and commit LSN;
- connector-offset checkpoint identity;
- complete per-topic-partition cut vector;
- Bronze and Silver schema versions;
- required output types and record-disposition evidence;
- validation rule-set and decision identity.

Validation cannot start until the candidate is sealed. After sealing, no input, output, checksum,
count or cut may change.

### Frozen publication contract

`payments-core-v1` activates these six serving members atomically:

```text
customers
accounts
merchants
payment_transactions
transaction_events
refunds
```

`payments.cdc_bootstrap_fences` belongs to the connector capture set but never to the serving
publication.

Serving readers resolve exact objects only through the active activation manifest. They cannot
discover staging, failed, cancelled, unverified legacy or partially written output through
production IAM or catalog paths.

Activation appends an immutable event and changes the active pointer with compare-and-swap in one
transaction. Rollback also fences stream continuation, changes its epoch, binds the target
successor cursor and requires the full replay range to remain available.

### Frozen source-cut contract

The initial active version is bounded by one completed PostgreSQL source transaction:

```text
fence transaction ID
+ fence commit LSN
+ immutable connector-offset checkpoint
+ per-topic-partition Kafka cut vector
```

Notification time, wall-clock time and observed topic end offsets are not publication cuts. No
source transaction may be split across the version boundary, and Kafka offsets are comparable
only within one topic-partition.

## Change control

ADR-001 must return to `Proposed` and receive a new design review if implementation needs to alter:

- serving or capture membership;
- generation or attempt correlation;
- source fence or cut construction;
- build states or allowed transitions;
- seal timing or manifest mutability;
- validation layers;
- activation transaction or pointer semantics;
- continuation or rollback behavior;
- production state-store guarantees;
- staging visibility or active-manifest authority;
- legacy migration safety.

Implementation details that preserve these contracts do not require an ADR amendment.

## Phase sequence

The controlled sequence is:

```text
ADR-001 freeze
-> ADR-002 design challenge
-> ADR-002 revision if required
-> ADR-002 freeze
-> ADR-003
-> ADR-004
-> ADR-005
-> freeze backlog v1.0
-> begin the first vertical implementation slice
```

No parallel feature branches are authorized by default. The first platform-remediation
implementation branch is created only after the overall Design Freeze exit criteria are
satisfied. GD-001 is an explicit, narrow Portal security exception and creates no precedent for a
second branch.

## Later gates

Completing the five blocker implementations permits a controlled **Production Pilot Review**, not
production sign-off. Final sign-off also requires pilot evidence, recovery drills, security
verification, long-running behavior, known limitations and a completed
`docs/production-readiness-report.md`.
