# ADR-PORTAL-002-02: Server-Side Session Store and Lifecycle

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

Revocation, timeout, rotation, encrypted OIDC material, concurrency, and audit cannot be enforced
by a self-contained browser JWT. Restore must not resurrect a formerly revoked session.

## Decision

Use a dedicated PostgreSQL `portal_control` database as authoritative session state. Production
must isolate its database/resource budget and connection pool from OLTP, Airflow metadata, and
pipeline control. Redis is not an authority in PR-PORTAL-002.

The cookie contains 256 random bits; persistence stores an HMAC lookup hash. Session rows use
optimistic version plus row locks. Token material is held in AES-256-GCM envelopes whose data keys
are wrapped by KMS. Session family/predecessor links make rotation and replay explicit.

Production timeout defaults are 30-minute idle, eight-hour absolute, five-minute identity
staleness, and at most five active sessions per principal. Terminal state never returns to ACTIVE.
A deployment-controlled security epoch invalidates all restored sessions after backup recovery.

## Alternatives

- Stateless JWT session: rejected because revocation and privilege removal remain stale until
  token expiry and logout cannot be authoritative.
- Redis-only session authority: rejected initially because durable revocation, audit transaction,
  backup/restore, and token-envelope recovery become separate systems.
- Airflow/control database reuse: rejected because it shares a failure domain and connection pool.

## Consequences

Protected Portal availability depends on HA PostgreSQL. The Portal now needs versioned security
migrations and a migration identity. This deliberately revises PR-001's “no Portal database”
boundary for security control state only.

## Security properties

- Opaque high-entropy identifier, rotation, terminal predecessors, atomic CAS.
- Revocation wins over refresh and concurrent callbacks.
- Absolute lifetime never extends.
- Cookie alone never establishes a session.
- Restore requires an external security-epoch change before service startup.

## Failure behavior

Store read/write uncertainty returns 503 or a retryable conflict and never assumes success. A
write timeout is resolved by idempotent re-read. No in-memory fallback is permitted.

## Migration

Introduce versioned schemas for login transactions, principals, sessions, token envelopes,
security epoch, policies, capabilities, audit ledger, and archive outbox. Runtime validates a
compatible revision but never migrates.

## Operational impact

Requires HA, PITR, isolated pool budgets, TTL cleanup, KMS rotation, active-session monitoring,
mass-revocation tooling, and a restore runbook.

## Testing

Exercise idle/absolute expiry, rotation, old-cookie denial, max sessions, concurrent refresh,
revocation races, store timeout, process restart, PITR restore, and security-epoch invalidation.

## Rollback

Disable login, increment security epoch, revoke/expire all cookies, retain schema and audit, and
deploy the previous schema-compatible artifact.
