# ADR-PORTAL-002-06: Authentication and Authorization Audit Architecture

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

Security state changes must leave immutable evidence without an unsafe synchronous dual write.
Retry, restart, archive outage, and database restore must not lose or rewrite history.

## Decision

Use an append-only security ledger in the dedicated Portal PostgreSQL database. Session/auth state
changes, the security event, and an archive-outbox record commit in one transaction. A durable
publisher archives events idempotently to a WORM-capable object namespace using event ID.

The runtime role has append-only access through a controlled function and no audit
UPDATE/DELETE/TRUNCATE permission. A trigger rejects mutation. Database sequence provides ledger
order. Daily archive manifests carry content hashes and integrity checkpoints.

Full audit covers authentication/session transitions, CSRF rejection, denied/indeterminate
decisions, production/audit/admin decisions, environment changes, and policy/capability changes.
Routine non-production R0 allowed reads may use aggregated telemetry.

## Alternatives

- Direct synchronous database plus object write: rejected because partial failure cannot be made
  atomic.
- Logs as audit: rejected because logs are mutable, lossy, and not a governed evidence contract.
- Kafka-only audit authority: deferred because transaction coupling to session state would require
  an outbox anyway.

## Consequences

Portal PostgreSQL is also the immediate security evidence authority. An archive publisher and
retention/integrity process become required production components.

## Security properties

- High-risk state cannot commit without its event.
- Events are versioned, minimized, append-only, ordered by ledger sequence, and deduplicated.
- Tokens, cookies, codes, verifiers, secrets, raw claims/bodies, and financial data are forbidden.
- Runtime cannot delete evidence.

## Failure behavior

Ledger failure rolls back high-risk state and returns 503. Archive failure leaves committed ledger
and outbox evidence, retries safely, and alerts on age. Duplicate publication is idempotent.

## Migration

Create ledger/outbox schema, roles, append function, immutability trigger, indexes/partitions,
archive publisher contract, safe query interface, and seven-year archive policy.

## Operational impact

Requires ledger/outbox age, write failures, integrity checkpoint, retention/legal hold, archive
access, and restore/export runbooks. Hot retention is 400 days; archive minimum is seven years.

## Testing

Prove transaction rollback on event failure, append-only permissions/triggers, forbidden
key/value scanner, deterministic deduplication, restart persistence, sequence ordering, archive
retry, integrity verification, and outage policy.

## Rollback

Never roll back by deleting events. Stop login/high-risk actions, preserve ledger/outbox, deploy a
schema-compatible publisher/API, and replay pending archive records.
