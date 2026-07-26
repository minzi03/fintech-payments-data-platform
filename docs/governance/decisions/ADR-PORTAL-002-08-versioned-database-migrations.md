# ADR-PORTAL-002-08: Versioned Database Migrations for portal_control

- Decision ID: `ADR-PORTAL-002-08`
- Status: **PROPOSED**
- Date: 2026-07-26
- Authority: repository governance authority
- Scope owner: Portal security implementation
- Supersedes: none
- Blocked by: GC-PORTAL-002-01 Blocker 1

## 1. Context

The Portal API (`apps/portal-api/`) requires a dedicated PostgreSQL database (`portal_control`)
with 11 tables for sessions, login transactions, token envelopes, security audit, capability
registry, and schema migrations. The frozen design freeze (section 16) defines the schema and
requires:

- versioned migrations executed by a migration identity before application rollout;
- runtime startup validates compatible schema revision but never migrates;
- application roles separated into migration owner, session runtime, audit append, archive
  publisher, and security read-only;
- forward-only production safety with expand-contract pattern;
- advisory lock to reject concurrent migrators;
- append-only migration history with version, checksum, and application compatibility.

No migration mechanism is currently defined. The repository has no Alembic configuration, no
migration directory, and no migration runner. The `pyproject.toml` includes `sqlalchemy` and
`psycopg` as dependencies but neither is currently used by migration infrastructure.

ADR-005 (general versioned database migrations) is Proposed and covers the platform-wide migration
strategy. This ADR is portal-specific and addresses the `portal_control` schema exclusively. It
aligns with ADR-005's principles but makes portal-specific tool and process decisions.

## 2. Decision drivers

- deterministic schema evolution across local, CI, staging, and production;
- repeatable and auditable migration history;
- forward-only production safety (expand-contract);
- rollback and recovery strategy for failed migrations;
- ownership by portal-api (single service migrates portal_control);
- least-privilege database access (migration role vs runtime role vs audit role);
- drift detection between expected and actual schema state;
- auditability of who applied which migration when;
- testability (migrations must be validated in CI before production);
- compatibility with FastAPI and the existing Python/SQLAlchemy stack;
- no silent schema mutation at application startup.

## 3. Options considered

### Option A — Alembic with SQLAlchemy Core metadata

Alembic is the standard migration tool for SQLAlchemy projects. It provides:

- revision-based migration history with autogenerate support;
- downgrade support (forward and back);
- migration locking via PostgreSQL advisory locks;
- checksum tracking per revision;
- integration with SQLAlchemy engine and connection management;
- well-understood ecosystem with extensive documentation.

The portal-api already depends on SQLAlchemy. Alembic adds no new ORM dependency — it uses
SQLAlchemy Core for schema reflection and DDL execution.

**Advantages:**
- native SQLAlchemy integration (already a dependency);
- rich ecosystem, well-documented, widely used in production;
- autogenerate detects schema drift from models;
- downgrade support for rollback scenarios;
- advisory lock support for concurrent migration protection;
- revision branching for complex migration chains.

**Disadvantages:**
- adds Alembic as a dependency (lightweight, < 100 KB);
- autogenerate requires SQLAlchemy models or manual DDL;
- Python-based runner means migration logic is coupled to Python runtime.

### Option B — Raw versioned SQL with a lightweight runner

A custom migration runner executing numbered SQL files against `schema_migrations`:

- SQL files in `migrations/versions/` with sequential numbering;
- custom Python runner reads, checksums, and executes;
- full control over migration logic and error handling.

**Advantages:**
- no external dependencies beyond psycopg;
- full SQL control, no abstraction layer;
- simpler mental model.

**Disadvantages:**
- must implement advisory lock, checksum, downgrade, and history tracking from scratch;
- no autogenerate or schema drift detection;
- higher maintenance burden and bug surface;
- less community support and fewer battle-tested patterns;
- must build tooling that Alembic already provides.

### Option C — Delegate to ADR-005 platform-wide mechanism

Wait for ADR-005 to be resolved and use whatever mechanism it selects.

**Advantages:**
- single mechanism across all schemas.

**Disadvantages:**
- ADR-005 is Proposed with no timeline;
- portal_control has different ownership, security, and deployment model than pipeline schemas;
- blocks portal runtime implementation indefinitely;
- portal-specific migration needs (security roles, audit trail) may differ from platform needs.

## 4. Decision

**Select Option A: Alembic with SQLAlchemy Core metadata.**

### 4.1 Migration tool

Alembic (`alembic>=1.14,<2.0`) as the migration runner. No SQLAlchemy ORM models are required —
Alembic operates on SQLAlchemy MetaData or manual DDL scripts.

### 4.2 Migration directory layout

```text
apps/portal-api/
  alembic.ini                 # Alembic configuration
  migrations/
    env.py                    # Alembic environment (engine, target_metadata)
    script.py.mako            # Migration template
    versions/
      001_initial_portal_control.py   # Baseline: 11 tables
      002_*.py                        # Future migrations
```

### 4.3 Revision convention

Sequential zero-padded numbering: `001_`, `002_`, etc.

Each revision file contains:
- `upgrade()` — forward DDL;
- `downgrade()` — reversible DDL (where safe);
- revision ID (Alembic-generated hash for dependency tracking);
- down_revision pointer for chain integrity.

### 4.4 Authoritative migration history table

`schema_migrations` (frozen in design freeze section 16):

| Column | Purpose |
|---|---|
| `version` | Migration version identifier |
| `checksum` | SHA-256 of migration script content |
| `applied_at` | UTC timestamp of application |
| `applied_by` | Migration role identity |
| `application_compat` | Application version range this migration is compatible with |

Alembic's internal `alembic_version` table tracks the current head. The `schema_migrations` table
is the frozen authoritative record — Alembic's table is supplementary.

### 4.5 Upgrade procedure

1. Migration owner acquires PostgreSQL advisory lock (`pg_advisory_lock`).
2. Validate all prior checksums in `schema_migrations` match expected values.
3. Execute pending migrations within a PostgreSQL transaction where supported.
4. Record version, checksum, applied_at, applied_by, and application_compat in `schema_migrations`.
5. Release advisory lock.

### 4.6 Downgrade policy

- **Local/development:** full downgrade supported for developer convenience.
- **CI:** downgrade tested to verify reversibility.
- **Staging:** downgrade permitted after team approval.
- **Production:** downgrade requires separately approved contract migration. Automatic destructive
  rollback is forbidden. Forward-repair is the primary recovery strategy.

### 4.7 Production rollback strategy

1. Deploy the previous compatible application artifact.
2. Verify application compatibility with current schema version.
3. If schema rollback is required, execute the approved contract migration (separate release).
4. Never roll back schema automatically with application deployment.

### 4.8 Startup behavior

Application startup:

1. Read the current schema version from `schema_migrations`.
2. Compare against the application's supported version range.
3. If compatible, proceed normally.
4. If incompatible, fail closed with a clear error message.
5. **Never execute migrations at startup.** Migration is a separate deployment step.

### 4.9 Application runtime permissions

The portal-api runtime role (`portal_runtime`) has:

- SELECT on all portal_control tables;
- INSERT on `portal_sessions`, `portal_token_envelopes`, `oidc_login_transactions`,
  `security_audit_events`, `audit_archive_outbox`;
- UPDATE on `portal_sessions` (status, version, timestamps);
- UPDATE on `portal_token_envelopes` (ciphertext, rotation);
- UPDATE on `oidc_login_transactions` (status);
- **No UPDATE on `security_audit_events`** (append-only — INSERT only);
- **No DDL permissions** (CREATE, ALTER, DROP);
- **No access to `schema_migrations`** (migration-owned).

### 4.10 Migration-runner permissions

The migration role (`portal_migration`) has:

- CREATE SCHEMA, CREATE TABLE, ALTER TABLE, DROP TABLE on `portal_control`;
- INSERT, UPDATE on `schema_migrations`;
- SELECT on `schema_migrations` (for checksum validation);
- Advisory lock usage (`pg_advisory_lock`);
- **No access to application data** beyond schema_migrations.

### 4.11 CI validation

The CI pipeline includes:

1. `alembic upgrade head` against a test PostgreSQL database.
2. `alembic history --indicate-range` to verify chain integrity.
3. `alembic check` (if available) or manual drift detection.
4. Migration downgrade/upgrade cycle test.
5. Schema checksum verification against `schema_migrations`.

### 4.12 Local development flow

1. `alembic upgrade head` creates the portal_control schema from scratch.
2. Developer works against a local PostgreSQL with full schema.
3. `alembic downgrade base` cleans up for a fresh start.

### 4.13 Test database flow

1. CI provisions a PostgreSQL service container.
2. `alembic upgrade head` applies all migrations.
3. Integration tests run against the migrated schema.
4. Migration tests verify upgrade/downgrade cycles.

### 4.14 Drift detection

- Alembic autogenerate compares expected schema (from migration chain) against actual database.
- Drift detection runs in CI and is available as a local developer tool.
- Detected drift fails CI — schema must be updated through a migration, not manual DDL.

### 4.15 Ownership of portal_control

- **Schema ownership:** portal-api team.
- **Migration authority:** portal_migration role, executed by deployment pipeline.
- **Runtime authority:** portal_runtime role, used by application.
- **Audit authority:** portal_audit role, read-only access for security audit.
- No other service may migrate or modify portal_control.

### 4.16 Failed and partially applied migrations

- Migrations execute within PostgreSQL transactions where supported (DDL is not always
  transactional in PostgreSQL — e.g., CREATE INDEX CONCURRENTLY).
- For non-transactional migrations: record the partial state, log the failure, and require
  manual intervention or forward-repair.
- The advisory lock prevents concurrent migrators from compounding failures.
- After a failed migration, the migration owner must assess: repair forward, or restore from
  backup and retry.

### 4.17 Concurrent migration protection

- PostgreSQL advisory lock (`pg_advisory_lock`) acquired at migration start.
- Lock is session-scoped and released on connection close.
- If lock acquisition fails, migration exits with an error (no retry loop).
- Only one migrator may execute at a time against portal_control.

## 5. Security and governance constraints

- FastAPI remains the authentication and authorization enforcement point (frozen invariant 3).
- Migration credentials (`portal_migration`) are separate from runtime credentials
  (`portal_runtime`).
- Production startup never silently mutates schema (frozen: "runtime startup validates compatible
  schema revision but never migrates").
- Failed migrations fail closed — application does not start with an incompatible schema.
- No automatic destructive rollback in production.
- Migration history is auditable via `schema_migrations` and deployment pipeline logs.
- Token-envelope columns are never selectable by migration or audit roles.
- DDL changes follow expand-contract pattern with separately approved contract migrations.

## 6. Consequences

### Positive

- Portal-api gains a deterministic, auditable migration mechanism.
- Schema drift is detectable in CI before production.
- Rollback strategy is defined and tested.
- Least-privilege roles are enforced (migration vs runtime vs audit).
- Aligns with ADR-005 principles while remaining portal-specific.

### Trade-offs

- Alembic adds a dependency (~100 KB), justified by existing SQLAlchemy stack.
- Python-based migration runner couples migration logic to Python runtime (acceptable for
  portal-api which is already Python).
- Autogenerate requires either SQLAlchemy models or manual DDL scripts — portal schema is defined
  in design freeze DDL, so manual DDL scripts are the primary source.

### Operational burden

- Migration role must be provisioned in all environments (local, CI, staging, production).
- CI pipeline must include PostgreSQL service container for migration testing.
- Deployment pipeline must execute migrations before application rollout.
- Schema version compatibility must be maintained across application releases.

### Required follow-up work

1. Add `alembic` to `apps/portal-api/pyproject.toml` dependencies.
2. Create `alembic.ini` and `migrations/env.py` configuration.
3. Create baseline migration `001_initial_portal_control.py` with all 11 tables.
4. Add migration role provisioning to deployment documentation.
5. Add CI migration validation step to `.github/workflows/ci.yml`.
6. Remove unused `sqlalchemy` and `psycopg` from current `pyproject.toml` if they are not
   needed by runtime code, or confirm they are used by migration infrastructure.

### Impact on existing non-conformant implementation

Commit `a8d3842` on `codex/pr-portal-002-security-boundary` does not include migration
infrastructure. This ADR authorizes the migration mechanism — the actual migration setup will be
implemented as part of the remediation of `a8d3842` after all required governance actions are
approved.

## 7. Status

**PROPOSED**

This ADR is a governance proposal. It does not authorize runtime implementation.

**Runtime implementation remains NOT AUTHORIZED by this ADR proposal.**

This ADR becomes EFFECTIVE only after:

1. Governance review and approval.
2. Merge into the protected default branch.
3. All other required governance actions from GC-PORTAL-002-01 are also approved and merged.

## 8. Review evidence

- GC-PORTAL-002-01 (merged, PR #5, 8a71fc3)
- ADR-005 (Proposed, general versioned migrations)
- Design freeze section 16 (portal_control schema requirements)
- ADR-PORTAL-002-01 through 07 (frozen portal security contracts)
- pyproject.toml (existing SQLAlchemy/psycopg dependencies)
- CI workflow (current validation pipeline)
