# PORTAL-002 Runtime Conformance Re-review

- Review type: final read-only conformance re-review
- Implementation branch: `feat/portal-002-runtime-conformance`
- Governance baseline: `main` at `d0a6dd1ea3ce9f7983baeb44cf6fd8d7afe0d48d`
- Runtime scope: local and development only
- Production deployment authorization: **NOT GRANTED**

## 1. Executive summary

The seven remediation batches replace the foundation-only baseline assessed in
`PORTAL-002-runtime-conformance-review.md` with an operational local/development security
runtime. The implementation now has versioned PostgreSQL persistence, protected login intent and
transaction state, OIDC with PKCE and browser binding, the ADR-PORTAL-002-10 Model C callback,
strict token validation, principal and entitlement resolution, `ALLOW`-only policy evaluation,
opaque server-side sessions, CSRF protection, direct FastAPI authorization, atomic audit evidence,
generated contracts, an authenticated Next.js experience, and a real Keycloak browser test.

No requirement remains `NON-CONFORMANT` or `NOT IMPLEMENTED` in the authorized
local/development scope. Eight requirements remain `PARTIALLY CONFORMANT` against the complete
frozen design because their production or operational authority is intentionally absent. Those
gaps include external key management and restart continuity, validated discovery, provider
refresh/logout, the durable archive publisher, authoritative capability data, and the full
production recovery test matrix.

The result is suitable for local/development demonstration and continued hardening. It is not a
production-ready implementation and does not grant production deployment.

## 2. Authoritative evidence

This re-review treats the merged PORTAL-002 governance decisions, Portal Design Freeze, Threat
Model, Authorization Matrix, and Failure Matrix as authoritative. Evidence includes:

- runtime and migration sources under `apps/portal-api`;
- API and database tests under `apps/portal-api/tests`;
- OpenAPI and generated client sources under `packages/portal-contracts`;
- Next.js runtime and tests under `apps/portal-web`;
- disposable PostgreSQL and Keycloak configuration in `docker-compose.yml` and
  `infrastructure/keycloak/realm-export.json`;
- the local and CI verification gates in `.github/workflows/ci.yml`.

The earlier review remains the historical pre-remediation baseline and is not rewritten.

## 3. Scoring

The score uses `CONFORMANT = 1`, `PARTIALLY CONFORMANT = 0.5`, and all other statuses `= 0`.

| Status | Count |
|---|---:|
| CONFORMANT | 28 |
| PARTIALLY CONFORMANT | 8 |
| NON-CONFORMANT | 0 |
| NOT IMPLEMENTED | 0 |
| NOT APPLICABLE | 0 |

**Weighted conformance score: 32 / 36 (88.9%).**

This is a governance-conformance score, not a production-readiness score.

## 4. Requirement assessment

| ID | Governance source | Requirement | Runtime evidence | Status |
|---|---|---|---|---|
| R01 | GC-PORTAL-002-04 | Issue a server-controlled, short-lived, one-use login context. | `auth.py:65-95`; `login_intent.py:71-126`; replay and expiry tests in `test_login_initiation.py`. | CONFORMANT |
| R02 | GC-PORTAL-002-04 | Protect and atomically consume login intent with exact browser-origin validation and transaction creation. | `login_intent.py:128-283`; rollback proof in `test_login_initiation.py:189-224`. | CONFORMANT |
| R03 | Design Freeze section 4; GC-PORTAL-002-04 | Accept only frozen login inputs and keep provider/return-path authority on the server. | `auth.py:97-130`; `http_models.py`; `provider_config.py`; negative request tests in `test_login_initiation.py`. | CONFORMANT |
| R04 | ADR-PORTAL-002-01; GC-PORTAL-002-02 | Generate PKCE S256 and persist the verifier with per-record envelope protection. | `pkce.py`; `protected_value.py:47-87`; `login_transaction.py:41-68`; unit and database tests prove ciphertext and context binding. Local/test ephemeral wrapping is authorized, but production KMS and restart-safe key continuity are intentionally absent. | PARTIALLY CONFORMANT |
| R05 | Design Freeze section 4; ADR-PORTAL-002-01 | Generate, bind, validate, and one-time consume state and nonce. | `login_transaction.py:41-71`; `callback.py:245-369`; `token_validation.py:92-102`; callback negative tests. | CONFORMANT |
| R06 | ADR-PORTAL-002-09 | Use a per-login HttpOnly browser binding, stored as a keyed representation and cleared after terminal outcomes. | `cookies.py:25-68`; `login_transaction.py:53-58`; `callback.py:245-369`; terminal cleanup in `auth.py:139-171` and `errors.py:107-116`. Key versions are recorded, but current/previous-key rotation is not implemented. | PARTIALLY CONFORMANT |
| R07 | ADR-PORTAL-002-10 sections 6.1-6.2 | Validate and exclusively claim one eligible transaction before exchange. | `callback.py:245-369`; claim transition and audit are one unit of work; lock/replay tests in `test_callback_orchestration.py`. | CONFORMANT |
| R08 | ADR-PORTAL-002-10 sections 5-7 | Implement Model C with one orchestration owner and separated adapter, external work, and finalization. | `callback.py:76-220`; ports in `ports.py`; HTTP adapter in `auth.py:132-171`. | CONFORMANT |
| R09 | Design Freeze section 4; ADR-PORTAL-002-10 section 8 | Use server-owned provider authority and keep network calls outside database transactions. | `provider_config.py`; `oidc_provider.py:31-103`; transaction-order tests prove the network boundary. Endpoints are bounded static server configuration, but validated discovery and its governed cache/TTL are not implemented. | PARTIALLY CONFORMANT |
| R10 | ADR-PORTAL-002-01; ADR-PORTAL-002-10 section 6.3 | Strictly validate ID-token signature and security claims before identity use. | `token_validation.py:45-170` validates algorithm, type, key ID, signature, issuer, audience/authorized party, time, nonce, group bounds, and assurance; unit tests cover failure cases and one JWKS refresh. | CONFORMANT |
| R11 | ADR-PORTAL-002-05 | Resolve stable issuer/subject principal and allowlisted server-owned entitlements. | `principal.py:28-88`; persisted principal and entitlement snapshots are exercised in callback integration tests. | CONFORMANT |
| R12 | ADR-PORTAL-002-03; Authorization Matrix | Only explicit `ALLOW` authorizes; every other policy result fails closed. | `policy.py`; `authorization.py:53-146`; finalization guard at `session_store.py:73-80`; matrix tests in `test_authorization.py`. | CONFORMANT |
| R13 | ADR-PORTAL-002-10 sections 6.4, 7, 12 | Atomically create/rotate session, consume the login transaction, and persist success audit/outbox. | One final unit of work in `session_store.py:104-260`; injected audit failure rollback in `test_callback_orchestration.py:346-389`. | CONFORMANT |
| R14 | ADR-PORTAL-002-02; Design Freeze section 5 | Keep opaque sessions in authoritative PostgreSQL state. | `metadata.py:117-211`; `session_store.py`; `session.py`; migration and database tests. | CONFORMANT |
| R15 | ADR-PORTAL-002-02 | Prevent fixation and enforce rotation, predecessor revocation, concurrency, and family limits. | `session.py:252-384`; `session_store.py:90-260`; session concurrency/rotation tests in `test_callback_orchestration.py`. | CONFORMANT |
| R16 | ADR-PORTAL-002-02; Design Freeze sections 5 and 14 | Enforce idle/absolute expiry, bounded staleness, and security-epoch invalidation without extending absolute lifetime. | `session.py:96-192,252-384`; tests prove absolute lifetime preservation. Durable session rows survive restart, but process-ephemeral lookup keys make existing cookies unresolvable after restart. | PARTIALLY CONFORMANT |
| R17 | ADR-PORTAL-002-07; Design Freeze section 6 | Emit only an opaque host-only session cookie with environment-safe attributes. | `cookies.py:71-118`; browser E2E verifies opaque HttpOnly SameSite cookie and empty browser storage. | CONFORMANT |
| R18 | ADR-PORTAL-002-07 | Require session-bound synchronizer CSRF and exact origin checks for unsafe operations. | `csrf.py`; `dependencies.py:51-66`; `session.py:198-249`; API and browser negative tests. | CONFORMANT |
| R19 | ADR-PORTAL-002-10 sections 9 and 13 | Deny duplicate callbacks and any non-pending/non-eligible transaction. | `callback.py:245-369`; replay tests in backend integration and `replay-and-csrf.spec.ts`. | CONFORMANT |
| R20 | ADR-PORTAL-002-10 sections 9, 14, 15 | Apply authoritative provider terminal states, conservative stale-claim recovery, and commit-unknown reread. | `callback.py:112-220,371-457`; `recovery.py:17-80`; provider classification and stale-claim tests in `test_callback_orchestration.py`. | CONFORMANT |
| R21 | Failure Matrix; Threat Model | Fail closed without partial authentication state and persist safe failure evidence. | `callback.py`; `errors.py`; transactional failure tests and generic browser failures prove no session is created. | CONFORMANT |
| R22 | ADR-PORTAL-002-06; Design Freeze section 13 | Use append-only audit and durable archive outbox without sensitive payloads. | `ledger.py`; append-only trigger in `001_initial_portal_control.py:380-396`; atomic outbox tests in `test_audit_ledger.py`. The durable publisher, WORM archive delivery, and replay lifecycle are not implemented. | PARTIALLY CONFORMANT |
| R23 | ADR-PORTAL-002-06; ADR-PORTAL-002-10 | Commit state transition, required audit, and outbox in the same corresponding transaction. | `ledger.py:16-58`; login, callback, session, and audit integration rollback tests. | CONFORMANT |
| R24 | ADR-PORTAL-002-08 | Use versioned, checksummed Alembic/SQLAlchemy Core migrations with compatibility validation and upgrade/downgrade tests. | `migrations/versions/001` through `005`; `migration_integrity.py`; `schema_guard.py`; `test_portal_control_migrations.py`; `alembic check` reports no drift. | CONFORMANT |
| R25 | ADR-PORTAL-002-08 | Separate migration/runtime/audit/archive privileges and prevent runtime DDL/audit mutation. | PostgreSQL bootstrap roles, migration grants/revokes, append-only database trigger, and privilege tests in `test_audit_ledger.py` and `test_portal_control_migrations.py`. | CONFORMANT |
| R26 | ADR-PORTAL-002-05; Authorization Matrix | Validate tenant and environment on every scoped operation; browser selectors do not grant. | `authorization.py:70-108`; `access.py:39-157`; integration tests reject unauthorized environment selection. | CONFORMANT |
| R27 | ADR-PORTAL-002-04; Design Freeze sections 9 and 12 | Keep capability authority revisioned and distinct from principal authorization; navigation is a server projection. | `access.py:98-158`; generated capability/navigation contracts; frontend renders only server projections. Current capability definitions are local static constants and do not consume the revisioned database registry tables. | PARTIALLY CONFORMANT |
| R28 | Design Freeze sections 10 and 15 | FastAPI independently enforces every protected operation. | `dependencies.py:28-101`; every protected router calls session, CSRF when unsafe, and authorization dependencies; direct-API tests prove denial independent of UI. | CONFORMANT |
| R29 | ADR-PORTAL-002-01/02/07; Design Freeze section 14 | Revoke locally before provider logout and rotate refresh/session state with reuse protection. | `session.py:252-500`; `session.py` commits local logout and rotation with audit; session endpoints are in `session.py` and `auth.py`. Provider refresh-token rotation, revocation/RP logout, and back-channel logout are not implemented. | PARTIALLY CONFORMANT |
| R30 | Frozen privacy requirements; ADR-PORTAL-002-10 section 16 | Keep tokens, codes, verifier, binding, raw claims, and session secrets out of browser authority and diagnostics. | `redaction.py`; `logging.py`; negative redaction tests; browser source/storage scan and real-login E2E; Playwright tracing is disabled for auth tests. | CONFORMANT |
| R31 | PORTAL-001 boundary; frozen invariant 1 | Keep browser traffic on the Portal/BFF boundary with no infrastructure credentials. | Same-origin generated client in `portal-api.ts`; proxy rules in `next.config.ts`; E2E checks the BFF boundary. | CONFORMANT |
| R32 | Design Freeze section 15 | Keep frozen OpenAPI operations and generated client synchronized. | Auth/session/environment/capability/navigation operations exist in OpenAPI; generation followed by scoped `git diff --exit-code` is clean. | CONFORMANT |
| R33 | Design Freeze sections 12 and 15 | Provide authenticated UX through generated contracts without browser security authority. | `app/login/page.tsx`; `app/auth/start/route.ts`; `session-context.tsx`; `authorized-navigation.tsx`; generated client calls in `portal-api.ts`; frontend trust-boundary tests. | CONFORMANT |
| R34 | Frozen invariant 8 | Reject development identity and the local security runtime outside local/development. | Startup validation in `config.py:144-164`; Compose fixes development identity false; unit tests reject staging/production security runtime. | CONFORMANT |
| R35 | Foundation security/error contract | Sanitize unexpected errors and expose only allowlisted metadata. | `errors.py`; `logging.py`; API integration and Problem Details frontend tests. | CONFORMANT |
| R36 | Design Freeze sections 18-21; Threat and Failure matrices | Prove runtime behavior with unit, database, migration, provider, replay, crash, audit, browser, and negative tests. | 126 backend tests, 25 frontend tests, and three real-browser tests pass; real Keycloak/PKCE, replay, CSRF, atomicity, provider-failure, stale-claim, migration, and audit cases are covered. Validated discovery, real signing-key rotation, provider refresh/back-channel logout, restart continuity, archive replay, and restore drills remain absent. | PARTIALLY CONFORMANT |

## 5. Findings

### 5.1 Blocking

No blocking finding remains for the explicitly authorized disposable local/development runtime.

### 5.2 Major

#### M-01 - Durable external security keys and restart continuity

Lookup and wrapping authority is generated in process memory. This is an explicitly authorized
local/test mechanism for verifier encryption, and it fails closed, but it prevents otherwise-live
login transactions and sessions from resolving after a process restart. Production requires
external key authority, versioned rotation, and restore drills.

#### M-02 - Validated OIDC discovery and signing-key lifecycle

Provider endpoints are server-owned and outbound calls are bounded, but the runtime does not
validate provider discovery or implement its frozen cache/stale behavior. Unit tests exercise one
JWKS refresh; real signing-key rotation and discovery outage behavior are not yet proved.

#### M-03 - Provider token, refresh, and logout lifecycle

Local logout and server-session rotation are authoritative and safe. Provider refresh-token
rotation/reuse, revocation, RP-initiated logout, and back-channel logout remain unimplemented.

#### M-04 - Audit archive publication

The append-only ledger and archive outbox are atomic and durable. The idempotent publisher, WORM
archive destination, retry/replay processing, and delivery monitoring remain unimplemented.

#### M-05 - Authoritative capability registry

Authorization, capability, and navigation concerns are separated, revision fields are carried,
and FastAPI remains authoritative. The local runtime currently projects a fixed capability set
instead of reading effective definitions and overrides from the revisioned database registry.

#### M-06 - Operational resilience evidence

The suite proves core state-machine, atomicity, negative, database, and real-browser behavior.
Process restart continuity, database restore/security-epoch drills, real key rotation, provider
back-channel behavior, and archive-outbox recovery remain required before production review.

### 5.3 Minor

No minor governance defect is recorded.

### 5.4 Observations

- The production startup gate deliberately rejects this local/development security runtime.
- The Keycloak realm contains only a disposable local test identity and no production entitlement.
- Browser auth tests disable traces so authorization codes and callback material are not retained
  in failure artifacts.
- CI now runs database-backed API tests and the real local OIDC browser flow against disposable
  services.

## 6. Verification evidence

| Gate | Result |
|---|---|
| Portal API unit/integration/migration suite | 126 passed |
| Ruff lint and format | Passed |
| Mypy | Passed for 57 source files |
| Alembic current head | `005_token_disposal_privilege` |
| Alembic autogenerate drift | No new upgrade operations |
| OpenAPI/generated client drift | No diff |
| Portal Web unit suite | 25 passed |
| Portal Web format, lint, and typecheck | Passed |
| Portal Web optimized build | Passed |
| Playwright real OIDC/security suite | 3 passed using one isolated worker |
| Compose configuration | Valid |
| Portal API and Web container UID | `10001`, non-root |
| API live/ready and Web health | Passed |
| Existing platform non-integration regression suite | 243 passed, 2 environment-dependent Airflow tests skipped |

## 7. Runtime readiness

| Question | Assessment |
|---|---|
| Is governance remediation complete? | Yes. |
| Is the callback implementation consistent with ADR-PORTAL-002-10 Model C? | Yes. |
| Is the runtime fully conformant with every production/operational requirement? | No. |
| Is the disposable local/development runtime ready for portfolio demonstration and further hardening? | Yes, with the documented limitations. |
| Did the maintainer authorize local/development implementation work? | Yes. |
| Is broader runtime authorization recommended before the major findings are resolved? | No. |
| Is production deployment authorized? | **No - NOT GRANTED.** |

## 8. Required remaining remediation

1. Replace ephemeral lookup and wrapping authority with approved durable external key management,
   including current/previous versions and restart/restore proof.
2. Implement validated OIDC discovery, bounded cache/stale semantics, and real signing-key rotation
   tests.
3. Implement provider refresh-token rotation/reuse protection, revocation, RP logout, and
   back-channel logout where supported.
4. Implement and operate the idempotent audit outbox publisher and immutable archive lifecycle.
5. Back capability/navigation projections with the authoritative revisioned registry and override
   data.
6. Complete restart, restore, outage, key-rotation, back-channel, and archive-replay drills.

Runtime implementation is not yet conformant.

Runtime authorization is NOT recommended.

Production deployment remains **NOT GRANTED**.
