# Architecture Design Freeze Process

## Purpose

No production-readiness remediation moves directly from ADR authoring to implementation. Every ADR
must pass an architecture-only challenge and receive design freeze before runtime code changes
begin.

## Lifecycle

```text
PROPOSED
→ DESIGN_REVIEW
→ REVISION_REQUIRED
→ ACCEPTED
→ DESIGN_FROZEN
→ IMPLEMENTING
→ FEATURE_VERIFIED
→ MERGED
```

`REJECTED` is terminal for the proposed architecture but does not close the backlog finding. A new
ADR must replace it.

## Design-review boundary

The review evaluates only:

- hidden assumptions;
- safety and completeness invariants;
- state and transition semantics;
- concurrency and race boundaries;
- replay and recovery;
- deployment and operational feasibility;
- backward compatibility;
- disaster recovery;
- long-term version evolution.

It does not review implementation details, code style, test style or unrelated components.

## Freeze requirements

An ADR is design-frozen only when:

1. Every P0 architecture flaw is resolved in the ADR.
2. Every remaining P1 is resolved or recorded as an explicit accepted risk with owner and expiry.
3. State machines list all terminal, retry, cancellation and recovery transitions.
4. Correctness boundaries and authoritative identities are explicit.
5. Concurrency, replay, rollback and disaster-recovery invariants are testable.
6. Migration and backward-compatibility behavior fail closed.
7. Operational ownership, evidence and escalation paths are defined.
8. Dependencies do not defer a correctness-critical guarantee beyond feature completion.

The accepted ADR is tagged with a frozen revision/checksum. A material design change during
implementation reopens design review.

## Delivery policy

ADR-001 through ADR-005 are challenged and frozen sequentially before the first runtime change.
After the overall Design Freeze gate closes, only one production blocker is implemented at a
time:

```text
five-blocker design freeze
→ feature branch for one blocker
→ schema/control changes
→ implementation
→ unit tests
→ integration tests
→ crash/recovery tests
→ focused feature review
→ merge
```

After the five pilot blockers merge, the repository enters a controlled Production Pilot Review,
not final production sign-off.
