# PR-PORTAL-002 Security Implementation Prompt

- Scope: Portal Identity, OIDC, Sessions, Authorization, Audit
- Authority: `docs/portal/pr-portal-002-design-freeze.md` (ACCEPTED — FROZEN Rev 1)
- Governance: `docs/governance/decisions/GD-001-portal-002-implementation-exception.md`
- NOT production deployment authorization

---

## Overview

Implement the frozen security architecture for the Fintech Data Platform Portal. The design is
fully frozen across 7 ADRs — **no design decisions remain open**. Every contract below is
mandatory and non-negotiable. Deviation from a frozen invariant requires reopening the
relevant ADR.

The existing codebase is at PR-PORTAL-001 foundation: a FastAPI BFF with 4 health/info endpoints,
a Next.js shell, generated TypeScript client, and containerized deployment. PR-PORTAL-002 adds
authentication, authorization, sessions, and audit on top of this foundation.

---

## Critical Invariants (NEVER violate)

1. Browser code NEVER receives an OIDC access, refresh, or ID token.
2. Browser state/navigation never grants access.
3. FastAPI is the ONLY authentication and authorization enforcement point.
4. Only ALLOW authorizes. DENY, NOT_APPLICABLE, unknown, stale, INDETERMINATE all fail closed.
5. Environment and tenant validated on EVERY scoped request, participates in EVERY policy decision.
6. Capability availability and principal authorization are SEPARATE decisions.
7. Session identifiers are opaque, high-entropy, server-side, rotated, unusable after rotation/revocation.
8. Absolute session lifetime NEVER extended. Identity staleness bounded.
9. Auth/privilege state changes commit ATOMICALLY with append-only audit evidence.
10. Production REJECTS development identity, insecure cookies, arbitrary issuer, missing encryption,
    mutable policy, unversioned security schema.

---

## Reference Documents (READ BEFORE IMPLEMENTING)

| Document | Path | Purpose |
|---|---|---|
| Design Freeze | `docs/portal/pr-portal-002-design-freeze.md` | Authoritative frozen architecture (985 lines) |
| Threat Model | `docs/portal/pr-portal-002-threat-model.md` | 35 threat scenarios with prevention/detection/recovery |
| Failure Matrix | `docs/portal/pr-portal-002-failure-matrix.md` | 35 failure modes with HTTP/cookie/audit/retry behavior |
| Authz Matrix | `docs/portal/pr-portal-002-authorization-matrix.md` | 10 context gates, 5 roles, 14 actions, environment/tenant/capability rules |
| ADR-001 (OIDC) | `docs/adr/portal-002-01-oidc-pkce-server-token-handling.md` | OIDC + PKCE + server-held tokens |
| ADR-002 (Session) | `docs/adr/portal-002-02-server-session-store-lifecycle.md` | PostgreSQL session authority |
| ADR-003 (Policy) | `docs/adr/portal-002-03-authorization-policy-model.md` | In-process deny-by-default policy |
| ADR-004 (Capability) | `docs/adr/portal-002-04-capability-registry-precedence.md` | Capability registry and precedence |
| ADR-005 (Env/Tenant) | `docs/adr/portal-002-05-environment-tenant-context.md` | Request-scoped environment and tenant |
| ADR-006 (Audit) | `docs/adr/portal-002-06-security-audit-architecture.md` | Append-only audit ledger |
| ADR-007 (Cookie/CSRF) | `docs/adr/portal-002-07-cookie-csrf-strategy.md` | Host cookie + synchronizer CSRF |
| GD-001 | `docs/governance/decisions/GD-001-portal-002-implementation-exception.md` | Bounded implementation exception |

---

## Implementation Slices (Frozen Order)

Implement in this exact sequence. Each slice must be independently testable before the next begins.

### Slice 1: Versioned Portal Security Schema + Migrations

**Goal:** PostgreSQL `portal_control` database with versioned migrations, 11 tables.

**Tables to create** (in `portal_control` schema):

```
portal_principals              — unique (issuer, subject), status, display attributes
oidc_login_transactions        — one-use, 5-min expiry, encrypted verifier
portal_sessions                — hash/family/predecessor, status/version, expiry, roles snapshot
portal_token_envelopes         — AES-256-GCM ciphertext per session family
portal_security_epochs         — environment-scoped monotonic epoch
portal_policy_revisions        — policy revision/digest, artifact version
portal_capability_definitions  — capability/environment/tenant/state metadata
portal_capability_overrides    — immutable admin overrides
security_audit_events          — append-only ledger with trigger protection
audit_archive_outbox           — publication state for durable archive
schema_migrations              — version/checksum/compatibility tracking
```

**Requirements:**
- Explicit primary/foreign keys, UTC timestamps (TIMESTAMPTZ)
- Uniqueness on: issuer/subject, session hash, transaction state, event ID, deduplication key
- Partial indexes for active sessions and unconsumed login transactions
- Optimistic version + row locking patterns
- Append-only trigger on `security_audit_events` (REJECT UPDATE/DELETE with SQLSTATE 55000)
- Versioned migration runner with advisory lock, prior checksum validation
- Application startup validates compatible schema revision but NEVER migrates
- Separate roles: migration owner, session runtime, audit append, archive publisher, read-only

**Files to create/modify:**
- `infrastructure/portal/` — SQL migration files (001_portal_control_schema.sql, etc.)
- `apps/portal-api/app/portal_api/db/migrations/` — migration runner
- `apps/portal-api/app/portal_api/db/schema.py` — schema constants and revision tracking
- Docker Compose: add `portal-postgres` service (separate from OLTP, separate from Airflow metadata)

**Acceptance criteria:**
- [ ] All 11 tables created with correct constraints, indexes, triggers
- [ ] Migration runner applies forward, validates checksum, rejects incompatible
- [ ] Append-only trigger rejects UPDATE/DELETE on audit events
- [ ] Separate PostgreSQL instance (or schema with separate connection pool minimum)
- [ ] Unit tests for migration runner, schema validation, trigger enforcement

---

### Slice 2: Session Store + Security Epoch

**Goal:** Authoritative PostgreSQL session lifecycle with security epoch.

**Implement:**
- `SessionStore` class — CRUD for sessions with CAS (compare-and-swap via optimistic version)
- Session state machine: ACTIVE -> REFRESH_REQUIRED -> ACTIVE (rotated) | EXPIRED_IDLE | EXPIRED_ABSOLUTE | REVOKED | PROVIDER_REVOKED | INVALID -> TERMINATED
- Login transaction state machine: PENDING -> CLAIMED -> CONSUMED | EXPIRED | INVALIDATED
- 256-bit random session ID, HMAC-SHA-256 lookup hash for persistence
- Session family tracking (predecessor links)
- Security epoch validation — session must match environment's current epoch
- Timeouts: 30-min idle, 8-hour absolute, 5-min login transaction, 5-min identity staleness (local: 15-min)
- Max 5 active sessions per principal (6th revokes oldest)
- Activity throttling: writes at most once per 60 seconds

**Files to create:**
- `apps/portal-api/app/portal_api/auth/session_store.py` — SessionStore with full lifecycle
- `apps/portal-api/app/portal_api/auth/models.py` — Session, LoginTransaction, SessionStatus enums
- `apps/portal-api/app/portal_api/auth/epoch.py` — Security epoch management

**Acceptance criteria:**
- [ ] Session creation, rotation, expiry (idle + absolute), revocation all work
- [ ] CAS prevents concurrent refresh conflicts (one winner)
- [ ] Revocation wins over concurrent refresh
- [ ] Security epoch invalidates all sessions when bumped
- [ ] Max 5 sessions per principal enforced
- [ ] Terminal states never return to ACTIVE
- [ ] Unit tests for every state transition, timeout, concurrency scenario

---

### Slice 3: Audit Ledger + Archive Outbox

**Goal:** Append-only security audit with transactional consistency.

**Implement:**
- `AuditLedger` class — append-only event writer, part of same DB transaction as session state changes
- 20 frozen event types (see design freeze section 13)
- Event envelope: event_id, ledger_sequence (monotonic), event_type, event_version, occurred_at,
  recorded_at, actor_type, principal_id, issuer_id, subject_reference, session_reference,
  tenant_id, environment_id, action, resource_type, capability_id, decision, reason_code,
  policy_revision, capability_revision, correlation_id, request_id, outcome, safe_metadata
- Forbidden fields: token, cookie, authorization, code_verifier, client_secret, password,
  OIDC token types, email, raw claims, request/response bodies
- Archive outbox: FOR UPDATE SKIP LOCKED publisher pattern
- Append-only trigger rejects UPDATE/DELETE
- Redaction scanner validates forbidden keys/values

**Files to create:**
- `apps/portal-api/app/portal_api/audit/ledger.py` — AuditLedger with append-only writes
- `apps/portal-api/app/portal_api/audit/models.py` — AuditEvent, AuditEventType enums
- `apps/portal-api/app/portal_api/audit/redaction.py` — Forbidden key/value scanner
- `apps/portal-api/app/portal_api/audit/archive.py` — ArchiveOutbox publisher

**Acceptance criteria:**
- [ ] Audit events append-only (trigger rejects mutation)
- [ ] All 20 event types representable
- [ ] Forbidden key/value scanner rejects token, cookie, password, etc.
- [ ] Archive outbox pattern with FOR UPDATE SKIP LOCKED
- [ ] Audit failure rolls back the state change (fail-closed)
- [ ] Unit tests: redaction, deduplication, ordering by ledger sequence, append-only enforcement

---

### Slice 4: OIDC Login + Real Provider Integration

**Goal:** Full OIDC Authorization Code Flow with PKCE S256.

**Implement:**
- `OidcProvider` class — discovery, JWKS caching (15-min TTL, 1-hr stale max), token exchange
- Login transaction: 256-bit state + nonce, PKCE verifier, browser-binding hash, 5-min expiry
- `POST /v1/auth/login` — create login transaction, return 303 to IdP authorization endpoint
- `GET /v1/auth/callback` — frozen 15-step validation (see design freeze section 4):
  1. Parse bounded response, reject duplicate params
  2. Constant-time validate state + browser binding
  3. Atomically claim unconsumed, unexpired transaction
  4. Validate redirect/provider binding
  5. Exchange code with PKCE, bounded timeout
  6. Validate signature via allowlisted issuer JWKS
  7. Validate exact issuer, audience, azp, nonce, algorithm, token type, time claims
  8. Max 60-second clock skew
  9. Enforce claim size and authentication-context requirements
  10. Map (issuer, subject) to stable Portal principal ID
  11. Map allowlisted group claim paths to roles and environment entitlements
  12. Create new session + audit evidence atomically
  13. Rotate/replace every pre-authentication browser identifier
  14. Mark transaction consumed (never reusable)
  15. Redirect only to stored normalized local path
- `POST /v1/auth/logout` — local logout first (revoke session, invalidate tokens, append audit),
  then best-effort provider logout
- `POST /v1/auth/logout-all` — revoke all principal sessions
- `GET /v1/auth/login-context` — short-lived login-intent view (anti-CSRF for login)
- Token handling: access/ID/refresh tokens stay inside FastAPI, AES-256-GCM envelopes
- Refresh token rotation mandatory; reuse revokes entire session family

**Keycloak in CI:**
- Add Keycloak service to docker-compose (test profile)
- Pre-configure test realm, users, groups matching the 5 roles
- Integration tests MUST use real Keycloak — mock-only OIDC does NOT satisfy merge gate

**Files to create:**
- `apps/portal-api/app/portal_api/auth/oidc_provider.py` — OIDC discovery, JWKS, token exchange
- `apps/portal-api/app/portal_api/auth/login_transaction.py` — Login transaction lifecycle
- `apps/portal-api/app/portal_api/auth/token_envelope.py` — AES-256-GCM token encryption
- `apps/portal-api/app/portal_api/auth/claims.py` — Claim parsing, principal/role mapping
- `apps/portal-api/app/portal_api/api/v1/auth.py` — Login, callback, logout endpoints
- `infrastructure/keycloak/` — Keycloak Docker config, test realm export

**Acceptance criteria:**
- [ ] Real Keycloak in docker-compose test profile
- [ ] PKCE S256 verified end-to-end
- [ ] State/nonce validation + replay rejection
- [ ] Browser NEVER receives tokens (only opaque cookie)
- [ ] Session rotates after login
- [ ] Refresh rotation with reuse detection (revoke family on reuse)
- [ ] 15-step callback validation fully implemented
- [ ] Logout commits locally before provider attempt
- [ ] All 20 OIDC-related failure scenarios from threat model tested
- [ ] Integration tests with real Keycloak: discovery, PKCE, callback, replay, key rotation, logout

---

### Slice 5: Cookie + CSRF Enforcement

**Goal:** Host-only session cookie and synchronizer token CSRF.

**Implement:**
- Session cookie:
  ```
  Name: __Host-fintech_portal_session_v1
  HttpOnly: true, Secure: true, SameSite: Lax, Path: /
  Domain: absent, Max-Age: remaining absolute lifetime
  ```
  Local loopback exception: `fintech_portal_session_v1` without Secure
- Cookie deletion repeats exact attributes with zero lifetime
- Cookie NEVER contains token, role, environment, capability, or authorization context
- CSRF synchronizer token:
  - 256-bit value, stored as hash + generation with session
  - Returned in `Cache-Control: no-store` session/CSRF response
  - Held in browser memory only (never localStorage/sessionStorage)
  - Sent in `X-CSRF-Token` header on unsafe methods
  - Bound to session family and version
  - Rotated with session rotation, invalid after logout
- Unsafe methods require valid token + exact allowed Origin
- Missing both Origin and Referer fails closed
- Wildcard/subdomain Origin matching forbidden

**Files to create/modify:**
- `apps/portal-api/app/portal_api/auth/cookie.py` — Cookie management
- `apps/portal-api/app/portal_api/auth/csrf.py` — Synchronizer CSRF token
- `apps/portal-api/app/portal_api/api/v1/session.py` — `GET /v1/session`, `GET /v1/session/csrf`, `POST /v1/session/refresh`
- `apps/portal-api/app/portal_api/core/middleware.py` — CSRF enforcement middleware

**Acceptance criteria:**
- [ ] Cookie attributes exactly match frozen spec
- [ ] Local loopback exception works, production rejects it
- [ ] CSRF token bound to session family/version
- [ ] Missing/foreign/stale CSRF token returns 403
- [ ] Malicious Origin rejected (no wildcard/subdomain matching)
- [ ] Login CSRF prevented (login POST uses login-intent, not session CSRF)
- [ ] Logout is POST with CSRF
- [ ] GET/HEAD/OPTIONS never require CSRF
- [ ] OIDC callback (GET) is the only safe-method side-effect exception
- [ ] CSRF matrix from failure matrix fully tested

---

### Slice 6: Principal/Claim/Role Mapping

**Goal:** Stable principal resolution from OIDC claims.

**Implement:**
- Principal store: `(issuer, subject)` -> stable `principal_id`
- Claim mapping configuration per allowlisted issuer:
  - Group claim paths -> Portal roles (5 roles: portal_viewer, data_engineer_viewer,
    platform_operator_viewer, security_auditor_viewer, portal_admin_viewer)
  - Environment entitlement claim paths
  - Assurance level mapping (AAL1, AAL2)
- Limits: max 100 groups, 256 chars/group, 20 effective roles
- Unknown groups grant nothing; no mapped role = session-only (view session/logout info only)
- Tenant from server deployment config, NOT from IdP claims
- Display attributes (email, name) are mutable, not authoritative

**Files to create:**
- `apps/portal-api/app/portal_api/auth/principal.py` — PrincipalStore, claim-to-role mapping
- `apps/portal-api/app/portal_api/auth/config.py` — OIDC provider config, claim paths, role mappings

**Acceptance criteria:**
- [ ] Principal created on first login, stable across sessions
- [ ] Group -> role mapping per issuer configuration
- [ ] Unknown groups ignored, unmapped users get minimal session
- [ ] Limits enforced (100 groups, 256 chars, 20 roles)
- [ ] Tenant from server config, never from browser/IdP

---

### Slice 7: Authorization Policy Module

**Goal:** In-process, deny-by-default, schema-validated policy.

**Implement:**
- Immutable policy bundle packaged with the Portal API artifact
- Policy revision = canonical digest of the bundle
- Decision engine evaluates: principal + roles + tenant + environment + domain + capability +
  resource attributes + action + classification + purpose + assurance + policy/capability revision
- Decision values: ALLOW, DENY, NOT_APPLICABLE, INDETERMINATE — only ALLOW grants
- Generated from frozen authorization matrix (docs/portal/pr-portal-002-authorization-matrix.md)
- 10 context gates applied BEFORE role grants (authentication, session, tenant, environment,
  entitlement, assurance, policy, capability, capability mode, resource)
- ALLOW cache: max 30 seconds, keyed on session version + tenant + environment + action +
  resource revision + policy revision + capability revision
- DENY cache: max 30 seconds, same dimensions
- Stale/expired/unavailable = fail closed

**Files to create:**
- `apps/portal-api/app/portal_api/auth/policy.py` — Policy engine, decision evaluation
- `apps/portal-api/app/portal_api/auth/policy_bundle.py` — Immutable bundle loading + validation
- `apps/portal-api/app/portal_api/auth/generated_matrix.py` — Generated tests from frozen matrix
- `apps/portal-api/policy/` — Policy bundle definition files

**Acceptance criteria:**
- [ ] 5 roles x 14 actions matrix correctly implemented
- [ ] 10 context gates evaluated before role grants
- [ ] Anonymous principal: only portal.authenticate + safe build info allowed
- [ ] DENY/NOT_APPLICABLE/unknown/stale/INDETERMINATE all fail closed
- [ ] ALLOW cache bounded at 30 seconds with correct dimensions
- [ ] Generated parameterized tests from frozen authorization matrix pass
- [ ] Policy unavailable = 503

---

### Slice 8: Environment/Tenant Context

**Goal:** Request-scoped environment and single-organization tenant enforcement.

**Implement:**
- Model B: environment explicit in every scoped request
- Route context: `/environments/{environment_id}/...`
- API context: `X-Portal-Environment-ID` header
- Stable IDs: local, development, staging, production
- Browser-supplied IDs are selectors, never grants
- Server intersects requested environment with session entitlements, tenant, assurance, policy, capability
- `POST /v1/session/environment` — validates selection, records audit, returns revisions
- `GET /v1/environments` — authorized environment views
- Single organization: canonical tenant `fintech-platform-primary` from immutable server config
- Tenant mismatch: masked 404 for resources, 403 for admin routes
- No selected environment = global safe endpoints work, environment-scoped APIs return ENVIRONMENT_REQUIRED

**Files to create:**
- `apps/portal-api/app/portal_api/auth/environment.py` — EnvironmentContext, tenant validation
- `apps/portal-api/app/portal_api/api/v1/environments.py` — Environment endpoints

**Acceptance criteria:**
- [ ] Environment validated on every scoped request
- [ ] Selection cannot create or elevate privilege
- [ ] Multiple tabs with different environments work safely
- [ ] Missing/invalid/cross-tenant fails closed (403 or masked 404)
- [ ] Deep links fail closed before resource retrieval

---

### Slice 9: Capability Registry

**Goal:** Immutable deployment bundle with revisioned environment overlays.

**Implement:**
- Immutable capability definitions (deployment bundle, schema-validated)
- Environment administrative overlays (revisioned)
- Effective-state precedence (strongest to weakest):
  1. Unknown/invalid/expired -> fail closed
  2. Admin DISABLED -> DISABLED
  3. Roadmap-only -> PLANNED
  4. Missing implementation -> PLANNED (pre-release) / DISABLED (post-expected)
  5. Dependency unavailable/stale -> DEGRADED
  6. Configured read-only -> READ_ONLY
  7. Implemented + enabled + healthy -> AVAILABLE
- `GET /v1/capabilities` — environment capability projection
- Authorization never changes capability state
- Registry health cache TTL: 30 seconds
- Initial capabilities:
  - `portal.foundation` — AVAILABLE (real contract present)
  - `portal.authentication` — AVAILABLE
  - `portal.system_status` — AVAILABLE
  - `portal.capabilities` — AVAILABLE
  - source, cdc, dataset, pipeline, audit, admin — PLANNED

**Files to create:**
- `apps/portal-api/app/portal_api/capabilities/registry.py` — CapabilityRegistry
- `apps/portal-api/app/portal_api/capabilities/models.py` — CapabilityState, CapabilityRecord
- `apps/portal-api/app/portal_api/api/v1/capabilities.py` — Capabilities endpoint

**Acceptance criteria:**
- [ ] Precedence rules correctly implemented
- [ ] Stale/unavailable registry = fail closed
- [ ] Authorization never changes capability state
- [ ] PR-PORTAL-001 capabilities show as AVAILABLE
- [ ] Domain capabilities show as PLANNED

---

### Slice 10: Navigation Projection

**Goal:** Server-derived navigation bound to session/version/capability/policy.

**Implement:**
- `GET /v1/navigation` — server-derived projection
- Response: groups, routes, labels, capability state, disabled reason, badges, safe access hints
- Cache key: session version + principal + tenant + environment + policy revision + capability revision
- Invalidated on: login, logout, rotation, refresh, environment switch, entitlement/policy/capability change
- Never contains raw claims or complete policy
- Frontend static metadata provides labels/layout only; effective visibility from server

**Files to create:**
- `apps/portal-api/app/portal_api/navigation/projection.py` — NavigationProjection
- `apps/portal-api/app/portal_api/api/v1/navigation.py` — Navigation endpoint

**Acceptance criteria:**
- [ ] Navigation reflects actual session state and capabilities
- [ ] Cache properly invalidated on state changes
- [ ] Never leaks raw claims or policy details

---

### Slice 11: Frontend Authenticated UX

**Goal:** Next.js portal handles authentication flow and protected routes.

**Implement:**
- Login page with redirect to IdP
- Authenticated app shell with principal info, environment selector, logout
- Session refresh handling (auto-refresh before expiry)
- 401 -> redirect to login
- 403 -> denial page
- Environment selector with server-projected options
- Dynamic navigation from `GET /v1/navigation`
- All existing PR-001 routes become session-protected EXCEPT `/health/live`, `/health/ready`, `/v1/system/info`
- CSRF token fetched and attached to unsafe requests
- Development routes gated behind authenticated session in addition to existing dev gate

**Files to create/modify:**
- `apps/portal-web/app/login/` — Login page
- `apps/portal-web/app/auth/callback/` — Client-side callback handler
- `apps/portal-web/components/auth-guard.tsx` — Authentication guard
- `apps/portal-web/components/environment-selector.tsx` — Environment picker
- `apps/portal-web/api/auth.ts` — Auth API client methods
- `apps/portal-web/middleware.ts` — Enhanced with session validation

**Acceptance criteria:**
- [ ] Unauthenticated users redirected to login
- [ ] Session refresh happens before expiry
- [ ] 401/403 handled gracefully
- [ ] Environment selector shows only authorized environments
- [ ] Navigation matches server projection
- [ ] No OIDC tokens in browser JavaScript, localStorage, sessionStorage, or cookies (beyond session cookie)
- [ ] Playwright E2E: real Keycloak login, authenticated shell, environment selection, logout

---

### Slice 12: Outage/Concurrency/Recovery Verification

**Goal:** Prove all 35 failure scenarios from the failure matrix.

**Implement comprehensive tests for:**
- IdP unavailable (before login, during callback, during refresh)
- Unknown signing key
- Session store unavailable/read timeout/write timeout
- Audit ledger unavailable
- Archive sink unavailable
- Policy unavailable/invalid
- Capability registry unavailable/stale
- Role/environment removed mid-session
- Invalid tenant mapping
- Concurrent revocation vs refresh
- Duplicate callback, duplicate logout
- Expired/foreign CSRF
- Multiple tabs (independent environments)
- Clock skew
- Refresh-token replay
- Browser close during callback
- Portal API restart (session survives server-side)
- Restore with old sessions (security-epoch bump)

**Concurrency tests:**
- Concurrent refresh serialization
- Revocation wins over refresh
- Max sessions eviction under concurrent login
- Security-epoch rotation under load

**Recovery tests:**
- PostgreSQL restore with security-epoch bump
- Audit ledger survives restart
- Session survives Portal API restart
- Archive outbox retry after outage

**Files to create:**
- `apps/portal-api/tests/security/` — Security test suite
- `apps/portal-api/tests/integration/test_outage_scenarios.py`
- `apps/portal-api/tests/integration/test_concurrency.py`
- `apps/portal-api/tests/integration/test_recovery.py`
- `apps/portal-web/tests/e2e/security/` — Browser security E2E tests

**Acceptance criteria:**
- [ ] All 35 failure scenarios from failure matrix have test coverage
- [ ] Concurrency tests prove correct behavior under contention
- [ ] Recovery tests prove state survives restarts and restores
- [ ] Playwright E2E proves no JavaScript-readable tokens

---

## Merge Gates (ALL must pass before merge)

From the frozen design freeze section 26:

1. Five contracts and seven ADRs remain accepted/frozen
2. OIDC integration uses a real standards-compliant provider (Keycloak) in CI
3. Authorization Code with PKCE S256 is verified end-to-end
4. State and nonce validation/replay tests pass
5. Browser receives no access, ID, or refresh token
6. Server session rotates after login
7. Idle and absolute timeouts are enforced
8. Logout invalidates server session before provider logout
9. Revocation cannot be bypassed by refresh, callback, or concurrent request
10. CSRF matrix passes
11. Generated authorization matrix passes
12. Environment selection cannot create or elevate privilege
13. Missing/invalid/cross-tenant context fails closed
14. Capability availability and authorization remain separate
15. Unknown/stale capability or policy state fails closed
16. 401/403/masked-404 semantics consistent
17. Navigation is server-projected and revision-bound
18. Direct unauthorized API calls denied independently of Next.js
19. Production rejects development identity and insecure config
20. Security audit append-only and transactional for high-risk state
21. Audit forbidden-key/value and redaction tests pass
22. Audit durability, deduplication, ordering, archive verification pass
23. Session-store, audit, policy, capability, and IdP outage behavior tested
24. Existing Portal and platform regression suites pass
25. GitHub Actions is green

**Additional repository gates:**
- Portal security migrations forward-tested and rollback-tested
- Production config validation rejects missing KMS, insecure cookie, arbitrary issuer,
  development identity, default tenant/epoch, mutable policy, anonymous dependency detail
- PostgreSQL restore tested with security-epoch bump
- GD-001 effective and unexpired; implementation within authorized scope

---

## Prohibited Scope (DO NOT implement)

Per GD-001, the following are explicitly OUT OF SCOPE:
- Source, CDC, Dataset, Pipeline, Recovery domain APIs
- DLQ redrive, warehouse, dbt, SQL, data-preview features
- Production mutations outside auth/session/security lifecycle
- Speculative domain models or generic infrastructure proxy endpoints
- Direct administration of Kafka, MinIO, PostgreSQL, Airflow, Docker
- Changes to ADR-001 through ADR-005 contracts
- Weakening or bypassing PR-PORTAL-002 frozen invariants
- Production rollout, production credentials, or production user enablement

---

## Tech Stack Decisions

| Component | Technology |
|---|---|
| Session store | PostgreSQL `portal_control` schema (dedicated DB or isolated pool) |
| Token encryption | AES-256-GCM with wrapped data keys (local: ephemeral test key) |
| OIDC provider | Keycloak (containerized in CI/local, real provider in staging/prod) |
| Policy engine | In-process pure Python, deny-by-default, immutable bundle |
| Cookie | `__Host-fintech_portal_session_v1`, HttpOnly/Secure/Lax |
| CSRF | Synchronizer token, 256-bit, session-bound |
| Audit | Append-only PostgreSQL ledger with trigger protection |
| Migration | Versioned forward-only with checksum validation |
| Frontend auth | Next.js middleware + server components, no client-side token handling |

---

## Estimated Scope

| Slice | Complexity | Files |
|---|---|---|
| 1. Security Schema | M | ~5 SQL + 3 Python |
| 2. Session Store | L | ~4 Python |
| 3. Audit Ledger | M | ~4 Python |
| 4. OIDC + Provider | XL | ~6 Python + Keycloak config |
| 5. Cookie + CSRF | M | ~3 Python |
| 6. Principal Mapping | S | ~2 Python |
| 7. Policy Module | L | ~4 Python |
| 8. Environment/Tenant | M | ~2 Python |
| 9. Capability Registry | M | ~3 Python |
| 10. Navigation | S | ~2 Python |
| 11. Frontend Auth UX | L | ~6 TypeScript |
| 12. Verification | L | ~8 test files |

**Total:** ~50 new/modified files, comprehensive test coverage required.
