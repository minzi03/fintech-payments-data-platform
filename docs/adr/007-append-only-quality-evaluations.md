# ADR-007: Append-Only Quality Evaluations and Decisions

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-020`

## Context

The control database currently upserts one quality result per pipeline run and rule name. Retry or
historical re-evaluation can overwrite the evidence used for the original pipeline decision.

## Decision

Quality evaluations are append-only and include rule version, configuration hash, attempt, input
dataset version and evaluation timestamp. A separate immutable decision record references the
exact evaluation IDs used to produce `PASS`, `WARN` or `FAIL`.

An optional current projection may expose the latest evaluation, but it is not audit evidence and
cannot change a historical decision.

## Alternatives considered

- Preserve only the latest result and rely on logs: rejected because logs are not a stable decision
  ledger.
- Copy values into pipeline runs: rejected because it loses per-rule identity and re-evaluation
  history.

## Consequences

Quality tables grow append-only and require partitioning/retention policy. Historical decisions can
be reconstructed exactly.

## Migration plan

Import existing quality rows as attempt 1 with an explicit legacy rule version, create matching
decision records for terminal pipeline runs, then remove the upsert path.

## Operational impact

Operators can compare evaluations without rewriting history and can prove which rule versions and
thresholds controlled a release.
