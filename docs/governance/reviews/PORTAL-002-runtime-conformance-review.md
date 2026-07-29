# PORTAL-002 Runtime Conformance Review

- Review type: read-only runtime assessment
- Evidence baseline: `main` at `d0a6dd1ea3ce9f7983baeb44cf6fd8d7afe0d48d`
- Governance state: remediation complete after PR #10 merge
- Runtime scope: local and development assessment
- Production deployment authorization: **NOT GRANTED**

## 1. Executive summary

The current Portal runtime is the PR-PORTAL-001 foundation, not a partial implementation of the
PR-PORTAL-002 security runtime. It exposes health and safe system metadata, uses a generated
same-origin client, applies baseline HTTP hardening, sanitizes errors, and rejects the development
identity switch in production configuration. It does not implement the OIDC, login-intent,
login-transaction, session, authorization, audit, migration, failure-recovery, or authenticated
frontend contracts required by the effective PORTAL-002 governance.

The dominant status is therefore `NOT IMPLEMENTED`, rather than `NON-CONFORMANT`. No implemented
authentication feature was found that deliberately weakens an effective decision; the security
features themselves are absent.

Runtime implementation is not yet conformant. Runtime authorization is **NOT recommended**.
Local/development remediation may proceed under the explicit maintainer authorization that
commissioned this review. Production deployment remains **NOT GRANTED**.

## 2. Authoritative evidence

This review treats the following merged decisions and frozen documents as authoritative:

- `docs/governance/decisions/GC-PORTAL-002-01-oidc-sequencing-resolution.md`
- `docs/governance/decisions/ADR-PORTAL-002-08-versioned-database-migrations.md`
- `docs/governance/decisions/GC-PORTAL-002-02-pkce-verifier-protection.md`
- `docs/governance/decisions/ADR-PORTAL-002-09-browser-binding.md`
- `docs/governance/decisions/GC-PORTAL-002-04-login-intent-contract.md`
- `docs/governance/decisions/ADR-PORTAL-002-10-callback-sequencing.md`
- `docs/portal/pr-portal-002-design-freeze.md`
- `docs/portal/pr-portal-002-threat-model.md`
- `docs/portal/pr-portal-002-authorization-matrix.md`
- `docs/portal/pr-portal-002-failure-matrix.md`
- frozen ADR-PORTAL-002-01 through ADR-PORTAL-002-07

The repository was inspected for actual backend, frontend, contract, configuration, migration,
test, and deployment evidence. Historical commit `a8d3842` is not on `main` and is not current
runtime evidence.

## 3. Scoring method

Each independently assessable requirement receives one of the required statuses. The score uses
`CONFORMANT = 1`, `PARTIALLY CONFORMANT = 0.5`, and all other statuses `= 0`.
`NOT APPLICABLE` items would be excluded from the denominator.

| Status | Count |
|---|---:|
| CONFORMANT | 3 |
| PARTIALLY CONFORMANT | 3 |
| NON-CONFORMANT | 0 |
| NOT IMPLEMENTED | 30 |
| NOT APPLICABLE | 0 |

**Weighted conformance score: 4.5 / 36 (12.5%).**

This score describes implementation coverage of the frozen security runtime. It is not a
production-readiness score and does not discount the quality of the existing foundation.

## 4. Requirement-by-requirement assessment

| ID | Governance source | Requirement | Runtime evidence | Status |
|---|---|---|---|---|
| R01 | GC-PORTAL-002-04 §§2.2–2.6 | `GET /v1/auth/login-context` issues a server-controlled, short-lived, one-use intent view. | `main.py:77-79` registers only health and system routers; OpenAPI paths at `portal-api-v1.json:435-704` contain no auth route. | NOT IMPLEMENTED |
| R02 | GC-PORTAL-002-04 §§2.7–2.15 | Login intent is protected server-side, validated with exact Origin/Referer and consumed atomically with login-transaction creation. | No intent store, model, persistence dependency, or login endpoint exists under `apps/portal-api/app/portal_api`. | NOT IMPLEMENTED |
| R03 | Design Freeze §4; GC-PORTAL-002-04 §2.4 | `POST /v1/auth/login` accepts only the frozen browser inputs and selects provider and return path server-side. | OpenAPI contains only four GET operations; the app description explicitly says no mutations are enabled (`main.py:54-59`). | NOT IMPLEMENTED |
| R04 | ADR-PORTAL-002-01; GC-PORTAL-002-02 §§2.1–2.5 | Generate PKCE S256 and persist the verifier only in an encrypted per-record envelope. | `pyproject.toml:10-15` has no OIDC or cryptographic runtime dependency; no PKCE or envelope module exists. | NOT IMPLEMENTED |
| R05 | Design Freeze §4; ADR-PORTAL-002-01 | State and nonce are server-generated, bounded, transaction-bound, validated, and one-use. | No login-transaction model or state/nonce implementation exists. | NOT IMPLEMENTED |
| R06 | ADR-PORTAL-002-09 §§5.1–5.16 | Use a per-login HttpOnly browser-binding cookie and server-side keyed hash; validate before exchange and clean up on every terminal outcome. | No binding cookie/hash implementation exists; the foundation test confirms no session cookie is emitted (`test_api.py:203-208`). | NOT IMPLEMENTED |
| R07 | ADR-PORTAL-002-10 §§6.1–6.2 | Resolve and exclusively claim one eligible login transaction; state, binding, claim, and claim audit commit before exchange. | There is no database connection, transaction repository, claim state, or callback router. | NOT IMPLEMENTED |
| R08 | ADR-PORTAL-002-10 §§5–7 | Model C owns callback orchestration and separates adapter, claim, external work, and atomic finalization. | No callback route, orchestration port, or authentication package is registered (`main.py:11-19, 77-80`). | NOT IMPLEMENTED |
| R09 | Design Freeze §4; GC-PORTAL-002-01 Blocker 5; ADR-PORTAL-002-10 §8 | Discovery and code exchange use server-owned allowlisted provider configuration, bounded outbound policy, and no open database transaction. | No issuer configuration, discovery/JWKS adapter, outbound OIDC client, or exchange implementation exists. | NOT IMPLEMENTED |
| R10 | ADR-PORTAL-002-01; ADR-PORTAL-002-10 §6.3 | Validate signature, issuer, audience, authorized party, nonce, token type, time, size, and authentication context before identity use. | No ID-token validator or claims parser exists. | NOT IMPLEMENTED |
| R11 | Design Freeze §§4, 8; ADR-PORTAL-002-05 | Resolve stable `(issuer, subject)` principal, allowlisted roles/environment entitlements, server-owned tenant, and assurance. | No principal store, claim mapping, entitlement mapping, or tenant context exists. | NOT IMPLEMENTED |
| R12 | ADR-PORTAL-002-03; Authorization Matrix §§1–7; ADR-PORTAL-002-10 §11 | Evaluate current policy/capability context; only `ALLOW` authorizes, every other outcome fails closed. | No policy engine or authorization dependency/middleware exists. | NOT IMPLEMENTED |
| R13 | ADR-PORTAL-002-10 §§6.4, 7, 12 | Session creation/rotation, successful transaction consumption, success audit, and outbox evidence commit atomically. | No persistence layer, session store, audit ledger, or final callback transaction exists. | NOT IMPLEMENTED |
| R14 | ADR-PORTAL-002-02; Design Freeze §5 | PostgreSQL is authoritative for opaque server-side sessions and the frozen lifecycle/state machine. | Portal API dependencies contain no SQLAlchemy/psycopg (`pyproject.toml:10-15`); Compose gives Portal API no Portal database (`docker-compose.yml:84-128`). | NOT IMPLEMENTED |
| R15 | ADR-PORTAL-002-02; Design Freeze §§5, 19 | Rotate on authentication/refresh, revoke predecessors, prevent fixation, enforce CAS/concurrency and family limits. | No session identifier, family, predecessor, rotation, or concurrency implementation exists. | NOT IMPLEMENTED |
| R16 | ADR-PORTAL-002-02; Design Freeze §§5, 14 | Enforce idle and absolute expiry, never extend absolute lifetime, bound identity staleness, and support security epoch invalidation. | No session timestamps, epoch, refresh, or revocation store exists. | NOT IMPLEMENTED |
| R17 | ADR-PORTAL-002-07; Design Freeze §6 | Emit only the frozen opaque host-only session cookie with environment-safe attributes and exact deletion behavior. | Baseline security middleware emits headers but no auth cookies (`security.py:14-55`); no cookie module exists. | NOT IMPLEMENTED |
| R18 | ADR-PORTAL-002-07; Design Freeze §6 | Enforce synchronizer CSRF and exact Origin/Referer checks for unsafe authenticated operations. | CORS allows only safe methods (`security.py:18-24`), but no session-bound CSRF token or unsafe auth route exists. | NOT IMPLEMENTED |
| R19 | ADR-PORTAL-002-10 §§9, 13 | Consumed, expired, invalidated, and actively claimed transactions deny duplicate callbacks and code exchange. | No durable transaction state machine or replay test exists. | NOT IMPLEMENTED |
| R20 | ADR-PORTAL-002-10 §§9, 14–15 | Apply authoritative pre-dispatch `INVALIDATED`, post-dispatch/ambiguous failed `CONSUMED`, stale-claim recovery, and commit-unknown reread semantics. | No provider dispatch marker/adapter, terminal-state transition, stale-claim worker, or crash-recovery logic exists. | NOT IMPLEMENTED |
| R21 | Failure Matrix; Threat Model; ADR-PORTAL-002-10 §15 | Every auth failure remains fail closed, creates no partial authentication state, and records safe evidence. | Generic Problem Details exist (`errors.py:113-184`), but no auth failure states or safe auth evidence exist. | NOT IMPLEMENTED |
| R22 | ADR-PORTAL-002-06; Design Freeze §13 | Append security events to an immutable audit ledger and durable archive outbox without sensitive fields. | Operational logging exists, but no audit schema, event model, append-only enforcement, or outbox exists. | NOT IMPLEMENTED |
| R23 | ADR-PORTAL-002-06; ADR-PORTAL-002-10 §§6.2, 6.4, 12 | Required claim/final/failure audit writes share the corresponding state transaction and roll back together. | No shared database unit of work or audit writer exists. | NOT IMPLEMENTED |
| R24 | ADR-PORTAL-002-08 §§4.1–4.17 | Use Alembic with SQLAlchemy Core, authoritative checksummed history, separate migration execution, startup compatibility validation, and tested upgrade/downgrade. | No tracked Alembic or migration files exist; Portal dependencies omit Alembic and SQLAlchemy (`pyproject.toml:10-24`). | NOT IMPLEMENTED |
| R25 | ADR-PORTAL-002-08 §§4.9–4.15 | Separate migration, runtime, audit, and archive privileges; runtime cannot perform DDL or mutate audit history. | Portal API has no database connection or role configuration; Compose exposes only the OLTP Postgres service (`docker-compose.yml:163-190`). | NOT IMPLEMENTED |
| R26 | ADR-PORTAL-002-05; Authorization Matrix §§1, 4–5 | Validate environment and tenant on every scoped request; browser selectors never grant access. | Only health/system routes exist; no environment/tenant context or scoped protected route exists. | NOT IMPLEMENTED |
| R27 | ADR-PORTAL-002-04; Design Freeze §§9, 12 | Capability availability is revisioned and independent from principal authorization; navigation is a server projection. | Adapter health is not a capability registry; no capability or navigation endpoints/models exist. | NOT IMPLEMENTED |
| R28 | Design Freeze §§10, 15; Authorization Matrix | FastAPI independently enforces every protected API operation and browser navigation never grants. | Browser traffic is proxied to FastAPI (`next.config.ts:43-49`) and browser inputs do not currently grant access, but no protected route or authorization enforcement exists. | PARTIALLY CONFORMANT |
| R29 | ADR-PORTAL-002-01/02/07; Design Freeze §14 | Logout commits local revocation/audit first; refresh rotates tokens/session and reuse revokes the family. | No logout, logout-all, refresh, token-envelope, or family-reuse implementation exists. | NOT IMPLEMENTED |
| R30 | All frozen privacy requirements; ADR-PORTAL-002-10 §16 | Tokens, codes, verifier, binding values, raw claims, and secrets never enter browser authority, logs, errors, audit, traces, or metrics. | Logging redacts several credential labels (`logging.py:13-28, 49-57`) and errors are sanitized, but the scanner does not yet prove coverage of all frozen auth artifacts and no auth flow exists to test. | PARTIALLY CONFORMANT |
| R31 | PR-PORTAL-001 boundary; frozen invariant 1 | Browser uses the Portal/BFF path and receives no infrastructure credentials or direct infrastructure access. | Generated client is same-origin (`portal-api.ts:23-28`); Next rewrites `/portal-api` to the BFF (`next.config.ts:43-49`); E2E rejects direct infrastructure ports (`portal-foundation.spec.ts:3-38`). | CONFORMANT |
| R32 | Design Freeze §15 | OpenAPI defines the frozen auth/session/environment/capability/navigation contracts and the generated client remains synchronized. | Foundation OpenAPI and generated client are synchronized, but the contract has only four health/system GET paths (`portal-api-v1.json:435-704`). | PARTIALLY CONFORMANT |
| R33 | Design Freeze §§12, 15; frontend trust boundary | Frontend provides authenticated UX using generated contracts without receiving provider tokens or treating UI state as authorization. | The web app exposes foundation status only (`app/page.tsx:7-18`); no login, callback, session, environment, navigation, or logout UX exists. | NOT IMPLEMENTED |
| R34 | Frozen invariant 8; Design Freeze §§8, 26 | Production rejects development identity. | Production validation rejects `development_identity_enabled` (`config.py:87-101`), Compose pins it false (`docker-compose.yml:94-112`), and unit/integration coverage exists. | CONFORMANT |
| R35 | Foundation security/error contract | Unknown errors and public metadata are sanitized and do not expose infrastructure secrets. | Problem Details replaces unknown exception content (`errors.py:167-184`); tests verify sanitized failures and allowlisted metadata (`test_api.py:118-127, 149-162`). | CONFORMANT |
| R36 | Design Freeze §§18–21; Threat and Failure matrices | Unit, migration, database, provider integration, replay, crash, atomicity, audit, browser, and security-negative tests prove the runtime. | Existing tests cover the foundation only (`test_api.py:1-208`); no auth/security, migration, Keycloak, or recovery suite exists. | NOT IMPLEMENTED |

## 5. Findings

### 5.1 Blocking

#### B-01 — No versioned security persistence foundation

There is no Portal security schema, Alembic chain, SQLAlchemy Core metadata, Portal database
connection, schema compatibility gate, or least-privilege role separation. Every durable
authentication invariant depends on this foundation.

- Evidence: R14, R24, R25
- Risk: authentication state cannot be authoritative, atomic, replay-safe, or recoverable.
- Required remediation: remediation Batch 1.

#### B-02 — OIDC initiation and secret protection are absent

Login context, single-use intent, login initiation, state/nonce, browser binding, PKCE S256,
per-record verifier encryption, provider allowlisting, discovery, exchange, and token validation
are not implemented.

- Evidence: R01–R06, R09–R10
- Risk: no governed authentication path exists.
- Required remediation: remediation Batches 1–3.

#### B-03 — Model C callback and terminal-state semantics are absent

There is no exclusive claim, external-call boundary, policy-gated finalization, terminal failure
disposition, duplicate-callback protection, stale-claim recovery, or commit-unknown reread.

- Evidence: R07–R08, R13, R19–R21
- Risk: implementing callback ad hoc would reopen the exact consistency blocker resolved by
  ADR-PORTAL-002-10.
- Required remediation: remediation Batches 3–4.

#### B-04 — Server session and authorization enforcement are absent

The runtime has no authoritative session, fixation-safe rotation, expiry/epoch, principal mapping,
policy engine, environment/tenant gates, CSRF protection, logout, refresh, or direct API
authorization.

- Evidence: R11–R18, R26, R28–R29
- Risk: no protected capability can be safely enabled.
- Required remediation: remediation Batches 3 and 5.

#### B-05 — Atomic security audit evidence is absent

Operational logs are not the append-only audit ledger. Neither security audit nor its archive
outbox can commit with claim, session, consumption, revocation, or privilege state.

- Evidence: R22–R23
- Risk: state could not meet the frozen atomicity and evidence invariant.
- Required remediation: remediation Batches 1, 3, and 4.

#### B-06 — Required contracts and authenticated frontend are absent

The authoritative OpenAPI, generated client, and Next.js application have no PORTAL-002
auth/session/environment/navigation surface.

- Evidence: R32–R33
- Risk: frontend integration would otherwise invent an ungoverned contract or security input.
- Required remediation: remediation Batch 6 after backend behavior is stable.

#### B-07 — Required security proof is absent

No real-provider, migration, replay, binding mismatch, state mismatch, provider ambiguity,
crash-recovery, session atomicity, audit, or browser token-exposure tests exist.

- Evidence: R36
- Risk: conformance cannot be demonstrated even after code is added.
- Required remediation: verification is incremental in every batch and completed in Batch 7.

### 5.2 Major

#### M-01 — Production-safety validation is incomplete for future security settings

The current configuration correctly rejects development identity in production, but OIDC issuer,
redirect, cookie, KMS/envelope, database role, tenant, policy revision, and security-epoch settings
do not exist and therefore cannot yet fail closed.

- Evidence: `config.py:27-102`
- Risk: future configuration could start with incomplete or insecure security authority.
- Required remediation: add typed settings and environment validators with the batch that
  introduces each authority; do not add production credentials.

#### M-02 — Sensitive-data redaction is foundation-only

The existing logger removes common credential labels, but the frozen set also includes
authorization codes, state, nonce, verifier, binding material, raw claims, and token response
objects. The implementation needs structured allowlisting and negative tests, not only regex
redaction.

- Evidence: R30
- Risk: future auth failures or HTTP-library exceptions could leak transient secrets.
- Required remediation: Batches 1, 3, 4, and 7.

### 5.3 Minor

No minor runtime defect is recorded. The missing controls are structural and are classified above.

### 5.4 Observations

- O-01: The same-origin generated client and Next.js-to-FastAPI rewrite are a sound trust-boundary
  foundation.
- O-02: Public metadata, generic failure responses, host validation, security headers, and
  production rejection of development identity should be preserved.
- O-03: The current foundation truthfully advertises that mutations are not enabled; it does not
  create a false sense that PORTAL-002 is already active.

## 6. Runtime readiness and recommendation

| Question | Assessment |
|---|---|
| Is callback sequencing governance-complete? | Yes; ADR-PORTAL-002-10 is effective after PR #10 merge. |
| Is the current runtime conformant? | No. |
| Is it ready for authenticated local/development use? | No; foundation-only. |
| May authorized local/development remediation proceed? | Yes, under the explicit maintainer decision commissioning this work. |
| Is runtime authorization recommended as an operational gate? | No, not until blocking findings are closed and evidence is rerun. |
| Is production deployment authorized? | **No — NOT GRANTED.** |

## 7. Required remediation

1. Establish versioned Portal security persistence, least-privilege roles, startup compatibility,
   audit append-only protection, and local/test database execution.
2. Implement login context, single-use login intent, login transaction, browser binding, PKCE S256,
   and protected verifier lifecycle.
3. Implement the Model C orchestration boundary, external provider adapter, strict token
   validation, principal/entitlement resolution, `ALLOW`-only policy, and atomic finalization.
4. Implement terminal failure dispositions, duplicate/replay denial, stale-claim recovery,
   commit-unknown reread, and terminal binding cleanup.
5. Implement session lifecycle, CSRF, rotation, logout/revocation, environment/tenant enforcement,
   capability registry, and direct API authorization.
6. Extend OpenAPI first, regenerate the client, and implement the frontend against that generated
   surface without browser token authority.
7. Prove the result with database, provider, callback, replay, crash, atomicity, audit, security
   negative, frontend, and regression tests.

Runtime implementation is not yet conformant.

Runtime authorization is **NOT recommended**.

Production deployment remains **NOT GRANTED**.
