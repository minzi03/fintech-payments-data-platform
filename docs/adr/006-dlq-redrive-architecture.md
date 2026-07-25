# ADR-006: Governed DLQ Redrive

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-019`

## Context

Poison records are durably quarantined before their Kafka offsets are committed, but quarantine is
currently a terminal sink. A parser fix cannot recover valid data without manual tooling.

## Decision

Maintain immutable poison evidence and an append-only control lifecycle:

```text
QUARANTINED
→ TRIAGED
→ APPROVED
→ REDRIVING
→ RECOVERED or REJECTED
```

Every attempt records parser version, redrive-policy version, actor, source coordinate, evidence
checksum and resulting Bronze identity. A recovered coordinate cannot be redriven again unless a
new approved policy explicitly supersedes the prior result.

## Alternatives considered

- Reset the Kafka consumer group: rejected because it replays unrelated records and loses
  per-record approval evidence.
- Edit quarantined payloads: rejected because original evidence must remain immutable.

## Consequences

Redrive is a separate governed ingestion path that must share normal Bronze validation and
idempotency. Some poison records remain permanently rejected.

## Migration plan

Index existing quarantine objects into the control ledger as `QUARANTINED`, preserving original
checksums and coordinates, then enable approval and redrive commands.

## Operational impact

Dashboards track poison age, triage backlog, recovery rate, parser versions and repeated failures.

