# PORTAL-002 Runtime Remediation Plan

- Basis: `PORTAL-002-runtime-conformance-review.md`
- Execution scope: local and development only
- Implementation branch: `feat/portal-002-runtime-conformance`
- Production deployment authorization: **NOT GRANTED**

## Execution status

| Batch | Status |
|---|---|
| 1 — Database, persistence, encryption, and audit foundations | COMPLETE |
| 2 — Login context and login initiation | COMPLETE |
| 3 — Model C callback orchestration | COMPLETE |
| 4 — Failure, replay, and crash recovery | COMPLETE |
| 5 — Session lifecycle and authorization enforcement | COMPLETE |
| 6 — Contract-driven frontend integration | COMPLETE |
| 7 — Integrated verification and local development proof | PENDING |

## 1. Planning principles

1. Effective governance remains authoritative; implementation does not rewrite or weaken it.
2. Each batch must leave a testable, fail-closed boundary.
3. Provider HTTP calls never run inside a local database transaction.
4. Successful session persistence, successful login-transaction consumption, success audit, and
   required outbox evidence share one atomic final commit.
5. No raw token, authorization code, state, nonce, PKCE verifier, binding value, session secret, or
   raw claim enters logs, errors, traces, metrics, or audit payloads.
6. Versioned migrations are separate from application startup.
7. Contracts are changed deliberately and generated artifacts are regenerated from OpenAPI.
8. Production credentials, users, traffic, rollout, and deployment remain prohibited.

## 2. Dependency order

```text
Batch 1: persistence and audit foundation
    -> Batch 2: login initiation
        -> Batch 3: callback orchestration
            -> Batch 4: failure and recovery
                -> Batch 5: session and authorization
                    -> Batch 6: frontend and contracts
                        -> Batch 7: integrated verification
```

Focused tests are added in the batch that introduces behavior. Batch 7 completes cross-component
and real-provider proof; it is not a deferral of unit security tests.

## 3. Batch 1 — Database, persistence, encryption, and audit foundations

### Expected files

Create:

- `apps/portal-api/alembic.ini`
- `apps/portal-api/migrations/env.py`
- `apps/portal-api/migrations/script.py.mako`
- `apps/portal-api/migrations/versions/001_initial_portal_control.py`
- `apps/portal-api/app/portal_api/db/__init__.py`
- `apps/portal-api/app/portal_api/db/engine.py`
- `apps/portal-api/app/portal_api/db/metadata.py`
- `apps/portal-api/app/portal_api/db/schema_guard.py`
- `apps/portal-api/app/portal_api/db/unit_of_work.py`
- `apps/portal-api/app/portal_api/auth/__init__.py`
- `apps/portal-api/app/portal_api/auth/models.py`
- `apps/portal-api/app/portal_api/auth/protected_value.py`
- `apps/portal-api/app/portal_api/audit/__init__.py`
- `apps/portal-api/app/portal_api/audit/models.py`
- `apps/portal-api/app/portal_api/audit/ledger.py`
- `apps/portal-api/app/portal_api/audit/redaction.py`
- `apps/portal-api/tests/migrations/test_portal_control_migrations.py`
- `apps/portal-api/tests/unit/test_protected_value.py`
- `apps/portal-api/tests/unit/test_audit_redaction.py`
- `apps/portal-api/tests/integration/test_audit_ledger.py`

Modify:

- `apps/portal-api/pyproject.toml`
- `apps/portal-api/app/portal_api/core/config.py`
- `apps/portal-api/app/portal_api/main.py`
- `apps/portal-api/app/portal_api/core/logging.py`
- `.env.example`
- `docker-compose.yml`
- `Makefile`

### Governance requirements satisfied

- ADR-PORTAL-002-08 Alembic/SQLAlchemy Core chain, checksummed authoritative history, separate
  migration execution, schema compatibility gate, upgrade/downgrade tests, and role boundaries.
- GC-PORTAL-002-02 local/test envelope abstraction and no plaintext verifier persistence.
- ADR-PORTAL-002-06 append-only ledger/outbox foundation.
- ADR-PORTAL-002-10 claim/final transaction primitives without external calls.

### Database impact

- Add isolated `portal_control` persistence for local/development.
- Create the frozen baseline security tables, required constraints/indexes, append-only audit
  protection, migration history, and privilege grants.
- Keep the application runtime role free of DDL and audit mutation authority.

### Security risks

- Accidental runtime DDL authority.
- Plaintext secret persistence.
- A migration history that Alembic can advance without authoritative checksum evidence.
- Audit trigger or grants that allow mutation.
- Starting the API against an incompatible schema.

### Test plan

- Upgrade from base to head and downgrade to base on PostgreSQL.
- Verify migration chain, checksums, advisory serialization, and drift checks.
- Prove audit UPDATE/DELETE rejection and runtime-role privilege limits.
- Prove application startup validates but never migrates.
- Prove local/test protected-value round trip and wrong-context/key failure.
- Prove forbidden audit fields and values fail closed.

### Acceptance criteria

- A clean local/test PostgreSQL reaches the expected head via an explicit migration command.
- Runtime startup on missing/incompatible schema fails closed without applying DDL.
- Append-only audit enforcement survives direct SQL attempts.
- Runtime and migration roles have the frozen minimum privileges.
- Sensitive values are never persisted or logged in plaintext.
- Focused lint, type, migration, and database tests pass.

## 4. Batch 2 — Login context and login initiation

### Expected files

Create:

- `apps/portal-api/app/portal_api/auth/login_intent.py`
- `apps/portal-api/app/portal_api/auth/login_transaction.py`
- `apps/portal-api/app/portal_api/auth/pkce.py`
- `apps/portal-api/app/portal_api/auth/browser_binding.py`
- `apps/portal-api/app/portal_api/auth/cookies.py`
- `apps/portal-api/app/portal_api/auth/provider_config.py`
- `apps/portal-api/app/portal_api/api/v1/auth.py`
- `apps/portal-api/tests/unit/test_login_intent.py`
- `apps/portal-api/tests/unit/test_pkce.py`
- `apps/portal-api/tests/unit/test_browser_binding.py`
- `apps/portal-api/tests/integration/test_login_initiation.py`

Modify:

- `apps/portal-api/app/portal_api/core/config.py`
- `apps/portal-api/app/portal_api/core/errors.py`
- `apps/portal-api/app/portal_api/main.py`
- `apps/portal-api/app/portal_api/core/security.py`
- `apps/portal-api/migrations/versions/001_initial_portal_control.py` only while the baseline
  revision remains unshared; otherwise add `002_login_intent_storage.py`
- `packages/portal-contracts/openapi/portal-api-v1.json`
- generated files under `packages/portal-contracts/src/generated/`

### Governance requirements satisfied

- GC-PORTAL-002-04 login-context/login-intent lifecycle and allowed request fields.
- ADR-PORTAL-002-09 per-login HttpOnly binding cookie and protected comparison.
- GC-PORTAL-002-02 PKCE S256 and immediate encrypted persistence.
- Design Freeze login transaction, state, nonce, provider, return-path, expiry, and Origin rules.

### Database impact

- Persist protected login intents with single-use consumption.
- Persist pending login transactions with protected state lookup, nonce, encrypted verifier,
  browser-binding keyed hash/key version, provider/callback binding, normalized return path, and
  bounded expiry.

### Security risks

- Treating browser provider/return path/identity fields as authority.
- Intent replay or non-atomic intent-consumption/transaction creation.
- Logging or returning verifier, nonce, state, or binding material.
- Weak local exceptions leaking into staging/production configuration.

### Test plan

- Login-context token entropy/opacity, expiry, consumption, and replay.
- Exact Origin/Referer behavior and return-path normalization.
- Reject all non-allowlisted request fields.
- PKCE S256 generation and encrypted-at-rest verifier evidence.
- Binding cookie attributes by environment; server stores only protected representation.
- Failure of encryption/storage/audit rolls back login initiation.

### Acceptance criteria

- Login initiation creates exactly one server-authoritative transaction only after consuming one
  valid intent.
- Browser receives only safe login context, provider redirect, and HttpOnly binding cookie.
- No provider token exchange occurs in this batch.
- OpenAPI and generated client are synchronized.
- Focused negative and persistence tests pass.

## 5. Batch 3 — Model C callback orchestration

### Expected files

Create:

- `apps/portal-api/app/portal_api/auth/callback.py`
- `apps/portal-api/app/portal_api/auth/ports.py`
- `apps/portal-api/app/portal_api/auth/oidc_provider.py`
- `apps/portal-api/app/portal_api/auth/token_validation.py`
- `apps/portal-api/app/portal_api/auth/principal.py`
- `apps/portal-api/app/portal_api/auth/policy.py`
- `apps/portal-api/app/portal_api/auth/session_store.py`
- `apps/portal-api/tests/unit/test_token_validation.py`
- `apps/portal-api/tests/unit/test_policy.py`
- `apps/portal-api/tests/unit/test_callback_orchestrator.py`
- `apps/portal-api/tests/integration/test_callback_atomicity.py`

Modify:

- `apps/portal-api/app/portal_api/api/v1/auth.py`
- `apps/portal-api/app/portal_api/core/config.py`
- `apps/portal-api/app/portal_api/core/errors.py`
- `apps/portal-api/app/portal_api/main.py`
- `apps/portal-api/app/portal_api/audit/ledger.py`
- `packages/portal-contracts/openapi/portal-api-v1.json`
- generated files under `packages/portal-contracts/src/generated/`

### Governance requirements satisfied

- ADR-PORTAL-002-10 Model C and normative sequence.
- ADR-PORTAL-002-01 strict provider/token validation and server-held tokens.
- ADR-PORTAL-002-03 `ALLOW`-only policy.
- ADR-PORTAL-002-05 stable principal, server tenant, and environment entitlement.
- ADR-PORTAL-002-02/06 atomic session/audit state.

### Database impact

- Short claim transaction: state/binding/eligibility validation, exclusive claim, safe claim audit.
- No database transaction or lock during provider exchange.
- Final transaction: authoritative reread, principal/entitlement snapshot, encrypted token envelope
  when required, opaque session, required rotation/revocation, successful consumption, success
  audit, and outbox evidence.

### Security risks

- Exchange before state/binding/claim commit.
- Network call under database lock.
- Provider/browser input becoming authority.
- Any result except explicit `ALLOW` creating a session.
- Cookie emission before final commit.
- Token material crossing the orchestration boundary or entering diagnostics.

### Test plan

- Exact sequence and port-level call ordering.
- State/binding validation before exchange.
- No open transaction during provider call.
- Strict issuer/signature/audience/azp/nonce/type/time/size/auth-context validation.
- `DENY`, `NOT_APPLICABLE`, `INDETERMINATE`, `ERROR`, unknown, stale, and unavailable all deny.
- Audit/session/consumption rollback together on every injected database failure.
- Session cookie emitted only after confirmed commit.

### Acceptance criteria

- One orchestration owner controls callback success.
- A second callback cannot exchange the code.
- External provider communication is observably outside both local transactions.
- Successful finalization is one atomic commit.
- Browser receives only the opaque session cookie after commit.

## 6. Batch 4 — Failure, replay, and crash recovery

### Expected files

Create:

- `apps/portal-api/app/portal_api/auth/recovery.py`
- `apps/portal-api/tests/security/test_callback_replay.py`
- `apps/portal-api/tests/security/test_provider_failures.py`
- `apps/portal-api/tests/security/test_browser_binding_failures.py`
- `apps/portal-api/tests/integration/test_callback_recovery.py`

Modify:

- `apps/portal-api/app/portal_api/auth/callback.py`
- `apps/portal-api/app/portal_api/auth/login_transaction.py`
- `apps/portal-api/app/portal_api/auth/browser_binding.py`
- `apps/portal-api/app/portal_api/audit/ledger.py`
- `apps/portal-api/app/portal_api/core/errors.py`

### Governance requirements satisfied

- ADR-PORTAL-002-10 §§9, 13–16 terminal states, duplicate behavior, stale claim, crash/retry,
  commit-unknown reread, and privacy.
- ADR-PORTAL-002-09 terminal browser-binding cleanup.
- Portal Failure Matrix callback cases as qualified/superseded by ADR-PORTAL-002-10.

### Database impact

- Transition conclusively pre-dispatch provider failure to `INVALIDATED`.
- Transition authoritative post-dispatch rejection, ambiguous dispatch/completion, and ambiguous
  stale claims to failed `CONSUMED`.
- Record safe failure evidence in the same transaction as terminal disposition.
- Never return terminal transactions to a reusable state.

### Security risks

- Relying on process memory, logs, timing, or operator inference after crash.
- Retrying an ambiguous exchange.
- Cleanup failure changing authentication outcome.
- Local commit uncertainty causing a second session or second exchange.

### Test plan

- Pre-dispatch failure, authoritative rejection, timeout/connection-loss ambiguity.
- Crash before claim commit, after claim commit, during exchange, before final commit, after final
  commit, and before response.
- Stale claims with and without authoritative durable proof that dispatch could not occur.
- Duplicate callbacks for every durable transaction state.
- Browser-binding cleanup failure isolation.
- Commit-unknown authoritative reread.

### Acceptance criteria

- Every failure maps to one durable fail-closed outcome.
- Ambiguous exchange is never retried with the same transaction.
- Durable state, not memory/logs/timing, determines recovery.
- Cleanup is best-effort and cannot alter state, authorization, or replay resistance.

## 7. Batch 5 — Session lifecycle and authorization enforcement

### Expected files

Create:

- `apps/portal-api/app/portal_api/auth/session.py`
- `apps/portal-api/app/portal_api/auth/csrf.py`
- `apps/portal-api/app/portal_api/auth/environment.py`
- `apps/portal-api/app/portal_api/auth/authorization.py`
- `apps/portal-api/app/portal_api/capabilities/__init__.py`
- `apps/portal-api/app/portal_api/capabilities/models.py`
- `apps/portal-api/app/portal_api/capabilities/registry.py`
- `apps/portal-api/app/portal_api/navigation/__init__.py`
- `apps/portal-api/app/portal_api/navigation/projection.py`
- `apps/portal-api/app/portal_api/api/v1/session.py`
- `apps/portal-api/app/portal_api/api/v1/environments.py`
- `apps/portal-api/app/portal_api/api/v1/capabilities.py`
- `apps/portal-api/app/portal_api/api/v1/navigation.py`
- `apps/portal-api/tests/security/test_csrf.py`
- `apps/portal-api/tests/security/test_session_lifecycle.py`
- `apps/portal-api/tests/security/test_authorization_matrix.py`
- `apps/portal-api/tests/integration/test_session_concurrency.py`

Modify:

- `apps/portal-api/app/portal_api/core/middleware.py`
- `apps/portal-api/app/portal_api/api/v1/auth.py`
- `apps/portal-api/app/portal_api/main.py`
- `apps/portal-api/app/portal_api/auth/session_store.py`
- `apps/portal-api/app/portal_api/auth/policy.py`
- `packages/portal-contracts/openapi/portal-api-v1.json`
- generated files under `packages/portal-contracts/src/generated/`

### Governance requirements satisfied

- ADR-PORTAL-002-02 session state, rotation, lifetime, concurrency, family, and security epoch.
- ADR-PORTAL-002-07 cookie/CSRF.
- ADR-PORTAL-002-03 authorization matrix and direct API enforcement.
- ADR-PORTAL-002-04/05 capability and environment/tenant separation.
- Frozen logout, revocation, refresh, and navigation contracts.

### Database impact

- Session activity, rotation, revocation, logout-all, refresh-family, epoch, environment selection,
  capability override, and security audit writes.
- All high-risk state changes use one local transaction with append-only audit/outbox evidence.

### Security risks

- Absolute lifetime extension.
- Revoked predecessor reuse or refresh race.
- CSRF bypass through missing/weak Origin handling.
- Browser environment or navigation becoming authority.
- Authorization cached without all frozen revision dimensions.

### Test plan

- Idle/absolute expiry, epoch invalidation, max-session limit, activity throttling.
- Concurrent refresh and revocation-wins.
- Cookie set/delete attributes and local versus staging/production validation.
- Full CSRF and generated authorization matrices.
- Tenant/environment deep-link and selector negative tests.
- Capability precedence remains distinct from policy.
- Logout local commit before best-effort provider logout.

### Acceptance criteria

- Every protected FastAPI operation independently authenticates and authorizes.
- Only `ALLOW` permits action.
- Session identifiers are opaque, rotated, server-side, and terminal after revocation.
- Absolute lifetime never extends.
- Browser selectors and navigation never grant access.

## 8. Batch 6 — Contract-driven frontend integration

### Expected files

Create:

- `apps/portal-web/app/login/page.tsx`
- `apps/portal-web/app/auth/callback/page.tsx`
- `apps/portal-web/app/forbidden/page.tsx`
- `apps/portal-web/api/auth.ts`
- `apps/portal-web/features/auth/login-panel.tsx`
- `apps/portal-web/features/auth/session-provider.tsx`
- `apps/portal-web/components/auth-guard.tsx`
- `apps/portal-web/components/environment-selector.tsx`
- `apps/portal-web/components/server-navigation.tsx`
- `apps/portal-web/tests/auth-flow.test.tsx`
- `apps/portal-web/tests/environment-selector.test.tsx`

Modify:

- `apps/portal-web/api/portal-api.ts`
- `apps/portal-web/app/layout.tsx`
- `apps/portal-web/components/app-shell.tsx`
- `apps/portal-web/proxy.ts`
- `apps/portal-web/tests/e2e/portal-foundation.spec.ts`
- `packages/portal-contracts/openapi/portal-api-v1.json`
- generated files under `packages/portal-contracts/src/generated/`

### Governance requirements satisfied

- Browser never receives OIDC tokens.
- Generated API client is the contract boundary.
- Session, CSRF, environment, navigation, and generic failure UX use server authority.
- Development routes require both environment gating and an authorized session.

### Database impact

None directly. Frontend actions exercise only versioned FastAPI contracts.

### Security risks

- Client-side callback parsing/exchange.
- localStorage/sessionStorage token or authorization state.
- UI route guards mistaken for API enforcement.
- Exposing provider details or raw failure content.

### Test plan

- Component tests for login, session expiry, 401/403, environment selector, and navigation.
- Browser storage/token scan.
- Same-origin cookie/CSRF behavior.
- Direct protected API denial independent of UI.
- Contract generation drift check and production frontend build.

### Acceptance criteria

- No provider token is observable by browser JavaScript, storage, URL, or cookie.
- All API calls use generated contracts.
- UI projects server decisions and never creates authority.
- Generic failures disclose no provider or token detail.

### Completion evidence

- Login initiation uses a native form and a same-origin Next server route; login intent creation,
  provider selection, redirect handling, and the browser-binding cookie remain server mediated.
- The browser uses the generated client for session, CSRF, environment, capability, navigation,
  logout, and protected dependency contracts.
- CSRF material is fetched immediately before each unsafe operation and is not persisted.
- Environment selection updates UI state only after the Portal API accepts it. Protected dependency
  status, navigation, and capabilities remain environment-scoped server projections.
- Development-only pages require both a non-production build and a current server session.
- Frontend unit tests, source trust-boundary tests, type checking, linting, formatting, and the
  optimized build pass. Batch 7 owns end-to-end browser and real-provider proof.

## 9. Batch 7 — Integrated verification and local development proof

### Expected files

Create:

- `infrastructure/keycloak/realm-export.json`
- `apps/portal-api/tests/integration/test_oidc_keycloak.py`
- `apps/portal-api/tests/integration/test_outage_scenarios.py`
- `apps/portal-api/tests/integration/test_recovery.py`
- `apps/portal-api/tests/integration/test_security_epoch_restore.py`
- `apps/portal-web/tests/e2e/security/authentication.spec.ts`
- `apps/portal-web/tests/e2e/security/replay-and-csrf.spec.ts`

Modify:

- `docker-compose.yml`
- `.env.example`
- `Makefile`
- `.github/workflows/ci.yml` or the existing Portal workflow that owns equivalent gates
- relevant focused tests from Batches 1–6

### Governance requirements satisfied

- Design Freeze merge gates and test architecture.
- Threat Model and Failure Matrix proof.
- All conformance review findings B-01 through B-07 and M-01 through M-02.

### Database impact

- Clean migration, downgrade/upgrade, supported-state upgrade, restore/epoch, restart persistence,
  and outage scenarios against disposable local/CI PostgreSQL only.

### Security risks

- Mock-only OIDC hiding discovery/JWKS/PKCE defects.
- Test fixtures containing reusable secrets.
- Integration logs leaking token material.
- Treating a local green build as production authorization.

### Test plan

- Real standards-compliant local/CI provider flow with PKCE.
- Signing-key rotation, invalid token/claim, exchange rejection, exchange ambiguity, and outage.
- Replay, state mismatch, binding mismatch, verifier protection, session atomicity, and audit.
- Process restart, database failure, stale claim, restore/security epoch, and outbox retry.
- Full backend lint/type/test, migration checks, contract drift, frontend format/lint/type/test/build,
  Playwright, Compose validation/startup/health.

### Acceptance criteria

- Every frozen threat/failure case is mapped to an automated or explicitly documented manual proof.
- Clean and supported-state migrations pass.
- No secret scanner or browser token-exposure test fails.
- All existing Portal and platform regressions remain green.
- A final read-only conformance rereview closes findings with file/line/test evidence.
- Result may support a separate local/development runtime authorization decision; it does not grant
  production deployment.

## 10. Batch commit policy

Use one descriptive commit per independently passing batch. Do not mix unrelated platform work.
Before each commit:

1. run focused tests;
2. run relevant lint/format/type checks;
3. inspect the staged diff for secrets and unrelated files;
4. record which conformance findings close;
5. leave failing or deferred evidence visible.

## 11. Completion gate

Implementation is complete only when:

- B-01 through B-07 and M-01 through M-02 are closed with durable evidence;
- the requirement table can be rerun without `NON-CONFORMANT` or `NOT IMPLEMENTED` for in-scope
  runtime controls;
- backend, frontend, migration, integration, browser, contract, and regression gates pass;
- a separate runtime authorization decision is made.

Local/development success does not imply production readiness.

**Production deployment remains NOT GRANTED.**
