# GD-001: Bounded PR-PORTAL-002 Implementation Exception

- Decision ID: `GD-001`
- Status: **APPROVED**
- Decision: **AUTHORIZED WITH CONDITIONS**
- Decision date: 2026-07-24
- Effective condition: this decision is merged into the protected default branch
- Expiry: earliest of PR-PORTAL-002 merge, abandonment, explicit revocation, or 2026-10-22
- Authority: repository governance authority
- Scope owner: Portal security implementation
- Production deployment authorization: **NOT GRANTED**

## 1. Context

The repository-wide Design Freeze in `docs/design-freeze.md` remains `IN PROGRESS`: ADR-001 is
accepted and frozen, while ADR-002 through ADR-005 are not yet frozen. Its default rule therefore
blocks runtime feature implementation.

Separately, PR-PORTAL-001 established the Portal foundation and PR-PORTAL-002 completed an accepted
security Design Freeze covering OIDC, server-side sessions, authorization, environment and tenant
context, capability projection, navigation, and append-only security audit.

Marking the repository-wide Design Freeze complete would be false. Silently ignoring it would
break governance. This decision grants one bounded, auditable exception without changing the
status of any platform production blocker.

## 2. Decision

PR-PORTAL-002 implementation is authorized as a bounded exception to the repository-wide Design
Freeze, subject to every condition in this document.

This is not:

- completion of the repository-wide Design Freeze;
- authorization for a production pilot or deployment;
- approval of Source, CDC, Dataset, Pipeline, Recovery, or other domain APIs;
- permission to modify a frozen platform blocker contract;
- precedent for another feature branch.

## 3. Authorized implementation scope

Only the following may be implemented:

1. Versioned `portal_control` security schema and migrations.
2. Dedicated PostgreSQL session authority and security epoch.
3. OIDC Authorization Code Flow with PKCE S256.
4. Server-side login transactions and encrypted token envelopes.
5. Server-session lifecycle, rotation, refresh, expiry, logout, and revocation.
6. Host-only session cookie and synchronizer CSRF enforcement.
7. Principal, claim, role, tenant, environment, and assurance mapping.
8. Deny-by-default Portal authorization policy and policy revision.
9. Request-scoped environment and single-organization tenant enforcement.
10. Capability Registry definitions, precedence, safe projections, and revisions.
11. Server-projected navigation.
12. Append-only security audit ledger, archive outbox, and audit verification.
13. Authenticated Portal session UX and protected foundation routes.
14. OpenAPI/generated-client changes required only by the frozen security contract.
15. Keycloak/PostgreSQL integration fixtures required to verify the security contract.
16. Security, concurrency, recovery, browser, migration, and regression tests.
17. Security implementation runbooks, configuration reference, and operational evidence.

No capability may be presented as implemented unless its real PR-PORTAL-002 runtime contract and
tests exist.

## 4. Explicitly prohibited scope

The exception does not authorize:

- Source inventory, source commands, or Source domain APIs;
- CDC inventory, connector control, snapshots, replay, offsets, or CDC domain APIs;
- Dataset inventory, publication, activation, preview, or Dataset domain APIs;
- Pipeline inventory, trigger, retry, backfill, or Pipeline domain APIs;
- Recovery checkpoints, replay plans, restore commands, or Recovery domain APIs;
- DLQ redrive, warehouse, dbt, Dremio, Superset, SQL, or data-preview features;
- production mutations outside the frozen authentication/session/security lifecycle;
- speculative domain models or generic infrastructure proxy endpoints;
- direct Portal administration of Kafka, Kafka Connect, MinIO, PostgreSQL, Airflow, or Docker;
- changes to ADR-001 through ADR-005 contracts or their production-remediation implementation;
- weakening, bypassing, or silently amending the PR-PORTAL-002 frozen invariants;
- production rollout, production credentials, or production user enablement.

Incidental changes outside the allowed scope require a separate governance decision.

## 5. Referenced frozen contracts

The implementation must conform to:

- `docs/portal/pr-portal-002-design-freeze.md`
- `docs/portal/pr-portal-002-threat-model.md`
- `docs/portal/pr-portal-002-authorization-matrix.md`
- `docs/portal/pr-portal-002-failure-matrix.md`
- ADR-PORTAL-002-01 through ADR-PORTAL-002-07 in `docs/adr/`
- the Portal foundation boundaries in `docs/portal/architecture-boundaries.md`

If implementation requires changing a frozen security invariant, the affected Portal ADR returns
to `Proposed`, this authorization suspends automatically, and governance must reapprove the
revised design.

## 6. Security and testing obligations

PR-PORTAL-002 must satisfy all frozen merge gates, including:

- real containerized OIDC-provider integration, not mock-only validation;
- PKCE S256, state, nonce, issuer, audience, key rotation, and replay tests;
- proof that no OIDC token enters browser storage, JavaScript-readable cookies, logs, traces,
  errors, audit, or generated frontend models;
- session fixation, rotation, idle/absolute expiry, refresh replay, logout, revocation, concurrent
  request, and restore-security-epoch tests;
- CSRF, exact-Origin, malicious-subdomain, login-CSRF, and logout-CSRF tests;
- generated authorization-matrix and fail-closed unknown-state tests;
- environment/tenant isolation and multi-tab tests;
- capability/authorization separation and stale-registry tests;
- transactional append-only audit, redaction, deduplication, archive-outbox, outage, and
  integrity tests;
- forward migration, compatibility, rollback, and least-privilege database-role tests;
- existing Portal and platform regression suites;
- green required GitHub Actions.

The implementation PR must include a focused security review against the frozen threat and failure
matrices. Passing UI tests alone is insufficient.

## 7. Required approvals

This governance decision is approved by the repository governance authority and becomes effective
only after it is merged into the protected default branch.

PR-PORTAL-002 merge additionally requires:

1. one designated security reviewer;
2. one platform/database reviewer for schema, migration, encryption, and recovery behavior;
3. required CI/security jobs green;
4. recorded focused-review disposition for every P0/P1 finding;
5. repository owner or delegated maintainer approval.

The implementation author cannot self-approve the security and platform review gates. Production
deployment requires a later, separate authorization.

## 8. Start and expiry conditions

Implementation may start only from a protected-default-branch commit containing:

- this approved decision;
- the referenced frozen Portal contracts; and
- the merged PR-PORTAL-001 foundation runtime, generated API contract, and required foundation
  verification.

This decision becoming effective does not prove that PR-PORTAL-001 has merged. If the foundation
is absent from the protected default branch, the implementation start condition remains unmet.
PR-PORTAL-001 runtime must not be bundled into PR-PORTAL-002 to bypass that dependency.

The exception ends automatically when the earliest event occurs:

- PR-PORTAL-002 is merged;
- PR-PORTAL-002 is formally abandoned;
- governance revokes this decision;
- a frozen Portal security ADR is reopened;
- implementation changes a platform blocker;
- the expiry date is reached.

An expired exception cannot be revived by reopening the branch. It requires a new governance
decision.

## 9. Suspension, revocation, and rollback

Authorization suspends immediately when:

- implementation exceeds allowed scope;
- a security-critical invariant is unresolved;
- a required real-provider or recovery test is removed or bypassed;
- production defaults enable development identity or insecure session behavior;
- an implementation finding requires a frozen ADR change;
- audit/session migration cannot be made safely reversible or compatible;
- a security incident calls the trust model into question.

On suspension:

1. stop implementation and merging;
2. preserve evidence and test results;
3. revert or isolate out-of-scope changes;
4. reopen the affected ADR when necessary;
5. obtain a superseding governance decision before resuming.

Because this decision authorizes development only, revocation does not authorize deploying a
partially implemented security boundary.

## 10. Relationship to repository-wide Design Freeze

`docs/design-freeze.md` remains `IN PROGRESS`. ADR-002 through ADR-005 remain blocked exactly as
before. GD-001 creates one explicit exception and does not change their status, order, or frozen
contracts.

The default repository rule remains “no runtime feature implementation during Design Freeze.”
GD-001 is the sole current carve-out and is interpreted narrowly.

## 11. Relationship to PR-API-001

PR-API-001 is **not required** before PR-PORTAL-002.

PR-PORTAL-002 may define only the security endpoints and cross-cutting conventions needed by its
frozen contract. It must not invent Source, CDC, Dataset, Pipeline, Recovery, or DLQ domain APIs.

PR-API-001, when authorized, is limited to cross-cutting conventions such as resource identity,
context, cursor pagination, Problem Details, correlation, revisions/ETag, preconditions,
idempotency, operation resources, audit/evidence references, version negotiation, time/freshness,
and sanitization. Domain contracts remain immediately before their vertical slices.

## 12. Final implementation authorization

**AUTHORIZED WITH CONDITIONS**

Authorization is effective only under the start condition, remains bounded to Section 3, and
expires under Section 8. It grants no production deployment authority.
