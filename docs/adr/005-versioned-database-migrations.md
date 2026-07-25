# ADR-005: Versioned Database Migration Strategy

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-005`

## Context

Initialization scripts correctly create a fresh environment but cannot prove ordered, repeatable
upgrades of long-lived OLTP, control or manifest schemas.

## Decision

Adopt a migration runner with an append-only history table containing version, checksum,
application compatibility, actor and applied timestamp. Migrations acquire an advisory lock,
validate prior checksums and use PostgreSQL transactions wherever supported.

Production changes use:

```text
expand
→ deploy compatible readers/writers
→ backfill and validate
→ contract
```

Destructive changes require a separately approved contract migration. Application startup verifies
that the database version is within its supported range but never performs production migrations.

## Alternatives considered

- Continue idempotent init SQL: rejected because idempotence does not encode upgrade ordering or
  compatibility.
- Run migrations automatically in every service: rejected because concurrent startup creates
  ambiguous ownership.

## Consequences

Schema evolution becomes an explicit release artifact. Some changes require multiple releases and
temporary dual-read/write behavior.

## Migration plan

Baseline current schemas as version 1 only after structural checksum verification, then move future
DDL into ordered migrations and retain init scripts solely for invoking the migration chain on a
fresh database.

## Operational impact

Release promotion includes migration dry-run, lock monitoring, compatibility checks and a
forward-repair plan.

