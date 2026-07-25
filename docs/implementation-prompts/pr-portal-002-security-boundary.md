# Implementation Prompt: PR-PORTAL-002 Security Boundary

> Status: **PREPARED, NOT YET EXECUTABLE**
>
> This prompt may be executed only after `GD-001` is present on the protected default branch,
> remains unexpired, and has not been suspended or revoked. Preparing or storing this prompt does
> not activate the authorization. PR-PORTAL-002 remains a development authorization only;
> production deployment is not authorized.

Act as a Principal Security Architect, Principal Platform Engineer, Staff Backend Engineer, Staff
Frontend Engineer, Database Reliability Engineer, and Security Test Engineer.

Implement PR-PORTAL-002 as the complete Portal security boundary. Follow the frozen architecture
exactly. Do not reopen design, reinterpret an invariant, or add adjacent platform functionality.

## 1. Governing authority

Read these sources completely before changing runtime code:

1. `docs/governance/decisions/GD-001-portal-002-implementation-exception.md`
2. `docs/portal/pr-portal-002-design-freeze.md`
3. `docs/portal/pr-portal-002-threat-model.md`
4. `docs/portal/pr-portal-002-authorization-matrix.md`
5. `docs/portal/pr-portal-002-failure-matrix.md`
6. `docs/portal/architecture-boundaries.md`
7. `docs/portal/api-contract.md`
8. ADR-PORTAL-002-01 through ADR-PORTAL-002-07 under `docs/adr/`

The five frozen contracts are:

1. OIDC and server-session lifecycle.
2. Authorization policy.
3. Capability Registry.
4. Route and API enforcement.
5. Append-only security audit.

The detailed Design Freeze, threat model, authorization matrix, failure matrix, and seven ADRs are
normative. If this prompt appears to conflict with them, the frozen sources win.

## 2. Mandatory authorization preflight

Before implementation:

1. Verify that the current branch is based on a protected-default-branch commit containing
   approved `GD-001`.
2. Verify that the same protected-default-branch ancestry contains the merged PR-PORTAL-001
   foundation runtime, generated API contract, and passing foundation verification.
3. Verify that `GD-001` is effective, unexpired, and not revoked or suspended.
4. Verify that all five contracts and all seven Portal ADRs remain accepted and frozen.
5. Verify that the planned diff is entirely inside the authorized scope and does not absorb
   unmerged PR-PORTAL-001 runtime.
6. Record the base commit, PR-PORTAL-001 merge evidence, decision revision, policy revision, and
   contract revision used.

If any check fails, stop without changing runtime code and return `NOT AUTHORIZED`.

`PR-API-001` is not a prerequisite. This PR may add only the security APIs and cross-cutting
contract changes required by the frozen PR-PORTAL-002 design.

## 3. Objective

Convert the PR-PORTAL-001 static Portal foundation into an authenticated, deny-by-default control
plane with:

```text
OIDC Authorization Code + PKCE S256
-> one-use server login transaction
-> encrypted server-held token material
-> opaque server-side session
-> request-scoped environment and server-owned tenant
-> capability evaluation
-> authorization policy evaluation
-> server-projected navigation
-> protected Portal UX
-> transactional append-only security evidence
```

The browser must never become an identity, token, authorization, tenant, environment, capability,
or audit authority.

## 4. Non-negotiable invariants

The implementation must preserve all frozen invariants, including:

- Browser code never receives an access token, refresh token, ID token, authorization code, PKCE
  verifier, raw claim document, or encryption reference.
- Browser state, route visibility, hidden controls, and navigation projections never grant
  access.
- FastAPI is the authentication owner and policy enforcement point for every protected API.
- Only `ALLOW` authorizes. Unknown, stale, invalid, `DENY`, `NOT_APPLICABLE`, and
  `INDETERMINATE` fail closed.
- The session cookie is opaque, high entropy, server-side, rotated, and unusable after rotation,
  expiry, logout, or revocation.
- Absolute session lifetime never extends.
- Environment is explicit and request-scoped. Browser input selects but never grants access.
- Tenant is immutable server deployment context and is never selected by the browser.
- Capability availability and principal authorization are separate evaluations.
- High-risk authentication, session, authorization, and security-state changes commit atomically
  with append-only audit evidence.
- Production startup rejects development identity, insecure cookie or issuer configuration,
  missing key management, default security epoch, mutable/invalid policy, incompatible schema, or
  anonymous dependency detail.
- PR-PORTAL-002 grants no production mutation and no direct infrastructure administration.

## 5. Explicitly prohibited work

Do not implement, scaffold, proxy, or model:

- Source inventory, commands, or Source APIs.
- CDC connectors, snapshots, replay, offsets, recovery, or CDC APIs.
- Dataset publication, activation, preview, SQL, or Dataset APIs.
- Pipeline triggers, retries, backfills, or Pipeline APIs.
- Recovery checkpoints, restore commands, or Recovery APIs.
- DLQ redrive, warehouse, dbt, Dremio, Superset, or object-data browsing.
- Generic infrastructure proxy endpoints or direct Kafka, Kafka Connect, MinIO, PostgreSQL,
  Airflow, Docker, or warehouse administration.
- Changes to platform ADR-001 through ADR-005.
- Production rollout, production credentials, or production user enablement.
- Convenience fallbacks that weaken the frozen failure behavior.

Leave future domain capabilities `PLANNED` and operationally unavailable.

## 6. Required implementation sequence

Implement one reviewable slice at a time in the following order. Do not start with the login
screen.

### Slice 1 — Versioned Portal security migrations

Create a dedicated, versioned `portal_control` security schema and migration lifecycle for:

```text
portal_principals
oidc_login_transactions
portal_sessions
portal_token_envelopes
portal_security_epochs
portal_policy_revisions
portal_capability_definitions
portal_capability_overrides
security_audit_events
audit_archive_outbox
schema_migrations
```

Requirements:

- Separate migration owner, session runtime, audit append, archive publisher, and security
  read-only roles.
- Explicit keys, constraints, UTC timestamps, optimistic versions, expiry indexes, active-session
  indexes, and one-use transaction constraints.
- Runtime roles cannot read token-envelope fields unless required for the exact lifecycle path.
- Runtime roles cannot update, delete, or truncate audit evidence.
- Runtime startup validates compatible schema revision and never runs migrations.
- Migrations are forward-tested, checksum-tracked, compatibility-tested, and have a safe rollback
  or roll-forward procedure.
- Do not reuse OLTP, Airflow metadata, pipeline-control pools, or migration identities.

### Slice 2 — Session authority and security epoch

Implement PostgreSQL-backed session authority:

- 256-bit opaque cookie identifier; persist only the versioned HMAC-SHA-256 lookup hash.
- Session family, predecessor, version, status, security epoch, identity revision, policy
  revision, entitlement revision, assurance, and expiry fields.
- Frozen login-transaction and authenticated-session state machines.
- Row locking plus optimistic compare-and-set for every transition.
- Thirty-minute production idle timeout, eight-hour absolute lifetime, five-minute production
  identity staleness, and at most five active sessions per principal.
- One winner for concurrent refresh/callback; revocation wins every race.
- Security-epoch mismatch invalidates restored sessions before protected traffic is accepted.
- No in-memory or cookie-only authority if PostgreSQL is unavailable.

### Slice 3 — Append-only audit ledger and archive outbox

Build audit authority before enabling security state changes:

- Insert the security event and archive-outbox record in the same transaction as the authoritative
  state transition.
- Use unique event ID, deterministic deduplication key, event version, and monotonic database
  ledger sequence.
- Enforce append-only behavior with privileges and a database mutation-rejection trigger.
- Prohibit tokens, cookies, codes, verifiers, secrets, raw claims, email, request/response bodies,
  sensitive query values, financial payloads, SQL, and local paths.
- Implement an idempotent leased outbox publisher with bounded retry and delivery evidence.
- Generate immutable archive manifests with hashes and integrity checkpoints.
- Keep audit-event payload immutable; delivery state belongs only to the outbox.
- Fail high-risk state changes closed if their ledger event cannot commit.

### Slice 4 — OIDC login transaction and real-provider integration

Implement provider-neutral OIDC Authorization Code Flow:

- Mandatory PKCE S256, 256-bit state, 256-bit nonce, browser binding, one-use five-minute login
  transaction, and normalized allowlisted local `return_to`.
- Exact configured provider ID, issuer, HTTPS production endpoint, discovery issuer, redirect URI,
  audience, authorized party, algorithm, token type, nonce, and time validation.
- Fifteen-minute discovery/JWKS cache and at most one-hour stale use for previously trusted known
  keys. Unknown `kid` requires a successful refresh.
- Bounded provider response, duplicate-parameter rejection, bounded timeouts, 60-second maximum
  clock skew, and frozen claim-size limits.
- Atomically claim and consume the login transaction. Sequential or concurrent replay cannot
  create a second session.
- Sanitize every failure. Never log, audit, trace, or return state, nonce, verifier, code, or token
  material.
- Use real containerized Keycloak in integration tests. Mock-only OIDC tests do not satisfy this
  slice.

### Slice 5 — Session and encrypted token lifecycle

Implement:

- Per-record AES-256-GCM token envelopes with authenticated associated data and wrapped data keys.
- A production KMS/secret-manager interface and an explicitly test-only ephemeral key provider.
- Token generation, key ID, rotation, disposal, and least-privilege retrieval.
- Session rotation after login and every privilege-relevant refresh or future step-up.
- Request-driven serialized refresh; no extension of absolute lifetime.
- Refresh-token rotation when supported; replay revokes the complete session family.
- Local logout commit before best-effort provider revocation/logout.
- Idempotent logout, logout-all, administrator revocation boundary, and back-channel logout
  binding.
- Terminal predecessor identifiers remain invalid permanently.

### Slice 6 — Cookie and CSRF enforcement

Implement the frozen browser credential:

```text
__Host-fintech_portal_session_v1
HttpOnly
Secure
SameSite=Lax
Path=/
no Domain
```

Requirements:

- Permit the non-Secure, non-`__Host-` exception only on explicitly validated local loopback.
- Store no token, role, environment, tenant, capability, or profile in the cookie.
- Implement a 256-bit synchronizer CSRF token stored as a server hash and returned only by a
  `Cache-Control: no-store` response.
- Keep the CSRF token in browser memory only.
- Require current token, current session/version binding, and exact allowed `Origin` for every
  unsafe method. Use validated `Referer` only as the frozen limited fallback.
- Protect login initiation and logout from CSRF.
- Preserve the OIDC callback as the sole documented side-effecting GET exception.
- Delete cookies with the exact original host/path/security attributes.

### Slice 7 — Principal, claim, role, and entitlement mapping

Implement:

- Stable external identity key `(issuer, subject)` mapped to immutable Portal principal ID.
- Email and display name as mutable display attributes only.
- Allowlisted claim paths per issuer and strict limits: 32 KiB claims, 100 groups, 256 characters
  per group, and 20 effective roles.
- Non-hierarchical frozen roles and explicit environment entitlements.
- Unknown groups and roles grant nothing.
- Production access requires independent production entitlement and AAL2.
- A principal without a mapped role may view only the bounded session/logout contract.
- Tenant never comes from arbitrary claims.
- Identity and entitlement refresh invalidates affected sessions, decision caches, capability
  projections, and navigation projections according to the frozen lifecycle.

### Slice 8 — Authorization policy engine

Implement a pure, in-process, deny-by-default policy module:

- Load a schema-validated immutable policy bundle packaged with the Portal API artifact.
- Use its canonical digest as the policy revision.
- Evaluate every frozen dimension: principal, role/group, tenant, environment, domain, capability,
  resource attributes, action, classification, purpose, assurance, policy revision, and
  capability revision.
- Return typed `ALLOW`, `DENY`, `NOT_APPLICABLE`, or `INDETERMINATE` plus stable reason,
  obligations, expiry, and cacheability.
- Permit only exact `ALLOW`.
- Generate parameterized policy tests from the frozen authorization matrix.
- Include unknown roles/actions/resources, missing context, all environment/capability states,
  AAL1/AAL2, tenant mismatch, expired identity, and revision mismatch.
- Bind any cache to exact session version, principal reference, tenant, environment, action,
  resource revision, policy revision, and capability revision; never exceed 30 seconds.

### Slice 9 — Request-scoped environment and tenant enforcement

Implement:

- Canonical Portal routes under `/environments/{environment_id}/...`.
- `X-Portal-Environment-ID` on every environment-scoped API request.
- Stable environments `local`, `development`, `staging`, and `production`.
- Server-side intersection of requested environment, session entitlements, configured tenant,
  assurance, policy, environment registry, capability, and resource attributes.
- Canonical immutable tenant `fintech-platform-primary` from deployment configuration.
- No session-global selected environment.
- Multiple tabs may safely use different environments.
- Missing context, disabled/unknown environment, entitlement removal, tenant mismatch, deep-link
  tampering, and cross-context resource access fail closed with frozen 403/masked-404 semantics.
- Environment selection validates and audits context but never changes roles or grants.

### Slice 10 — Capability Registry

Implement:

- Immutable schema-validated capability definitions.
- Revisioned administrative overlays.
- Native implementation and future adapter-health evidence.
- Canonical registry digest and complete frozen capability record.
- Exact precedence for unknown/invalid, `DISABLED`, `PLANNED`, absent/incompatible implementation,
  `DEGRADED`, `READ_ONLY`, and `AVAILABLE`.
- Thirty-second freshness bound and revision-bound projections.
- Stale or unavailable authority can never enable a mutation or a new authorization grant.
- Authorization filters visibility/actions after capability evaluation and never rewrites
  capability state.
- Only real PR-PORTAL-002 foundation capabilities may become available. Source, CDC, Dataset,
  Pipeline, Recovery, audit UI, and administration remain `PLANNED`.

### Slice 11 — Server-projected navigation

Implement `GET /v1/navigation` from current server authority:

- Bind projection to session version, principal reference, tenant, explicit environment, policy
  revision, capability revision, assurance, and preview entitlement.
- Return only safe route/group/label data, effective capability state, bounded reason, badges, and
  allowlisted links.
- Never return raw claims, policy internals, or an authority that the browser can replay.
- Invalidate on login, logout, rotation, refresh, entitlement change, environment context change,
  policy change, capability change, and revocation.
- Keep frontend route metadata presentational only.
- Direct API access must produce the same denial without Next.js or navigation.

### Slice 12 — Authenticated frontend UX

Update the existing Next.js shell without creating domain workspaces:

- Login initiation, sanitized callback outcome, session loading, expiry, logout, and denied states.
- Protected Portal foundation routes.
- Request-scoped environment routes and a selector that never mutates session authority.
- Dynamic server-projected navigation and capability-state presentation.
- In-memory CSRF lifecycle integrated with the generated API client wrapper.
- Environment-, session-, policy-, and capability-revision-bound query keys.
- Cache invalidation and safe refetch across multiple tabs, refresh, logout, role removal, and
  environment changes.
- No OIDC SDK or token-bearing model in the browser.
- No localStorage/sessionStorage credential, CSRF, role, or entitlement authority.
- Loading, 401, 403, masked 404, 409, 503, stale capability, and session-expiry states must follow
  frozen HTTP semantics.

### Slice 13 — OpenAPI and generated client

Add only the frozen endpoints:

```text
GET  /v1/auth/login-context
POST /v1/auth/login
GET  /v1/auth/callback
POST /v1/auth/logout
POST /v1/auth/logout-all
GET  /v1/session
GET  /v1/session/csrf
POST /v1/session/refresh
GET  /v1/environments
POST /v1/session/environment
GET  /v1/capabilities
GET  /v1/navigation
```

Also enforce:

- `/health/live` remains anonymous and process-local.
- `/health/ready` remains anonymous and returns aggregate safe readiness only.
- `/v1/system/info` remains anonymous safe build/contract metadata.
- `/v1/system/dependencies` becomes authenticated, environment-scoped, and authorized by
  `portal.system_status.read`.
- Use the existing Problem Details contract and all frozen PR-PORTAL-002 error codes.
- FastAPI models and routes remain authoritative. Regenerate and commit OpenAPI and TypeScript
  clients; never hand-edit generated output.
- Generated models must contain no token, cookie, raw claim, encryption reference, secret, or
  authoritative policy.

### Slice 14 — Security, concurrency, recovery, and operational verification

Complete all required evidence before merge:

- Unit tests for state machines, mapping limits, policy matrix, capability precedence, projection,
  redaction, deduplication, and Problem Details.
- PostgreSQL integration tests using the production schema and distinct migration/runtime/audit
  roles.
- Real Keycloak integration for discovery, PKCE, state, nonce, issuer/audience/signature, key
  rotation, callback replay, refresh rotation/replay, group changes, logout, and back-channel
  revocation.
- Browser E2E proving authenticated shell behavior, direct route/API denial, multiple
  environments, expiry, role removal, provider outage, cookie attributes, and absence of tokens in
  browser-visible storage/state.
- Concurrency tests for duplicate callback, refresh, revocation, logout, maximum-session
  enforcement, and audit/outbox leasing.
- Failure injection for IdP, JWKS, PostgreSQL reads/writes, audit append, policy, capability
  registry, archive sink, KMS/decryption, clock skew, and commit-unknown outcomes.
- Recovery test restoring old session data, incrementing the external security epoch, and proving
  every old cookie is denied.
- Migration compatibility and rollback/roll-forward verification.
- Full Portal and platform regression suites.

## 7. Required route-enforcement order

Every protected API must apply the frozen sequence:

1. Parse and bound request metadata.
2. Resolve the opaque cookie to current server session.
3. Validate state, epoch, idle/absolute expiry, freshness, and revocation.
4. Resolve configured tenant and explicit environment.
5. Resolve effective capability and revision.
6. Decide whether resource existence is safe to reveal.
7. Evaluate policy before protected resource retrieval where possible.
8. Retrieve only through an approved typed repository/service boundary.
9. Re-evaluate resource attributes when required.
10. Apply response-field obligations.
11. Commit required append-only security evidence.

Next.js route guards are UX only. They cannot replace any FastAPI enforcement step.

## 8. HTTP and failure behavior

Preserve the frozen semantics:

- `401`: missing, invalid, expired, revoked, or provider-invalid session.
- `403`: valid session denied by policy, environment, tenant, capability, assurance, Origin, or
  CSRF.
- `404`: missing resource or intentionally concealed protected resource.
- `409`: resolved retryable session/environment/policy concurrency conflict.
- `503`: required IdP, session, policy, capability, audit, or key authority unavailable.

Use sanitized Problem Details and correlation/request IDs. Never include upstream bodies, SQL,
tracebacks, local paths, secrets, tokens, cookies, or financial payloads.

Do not convert availability failures into authentication or authorization success. A safe degraded
read is permitted only where the frozen policy explicitly allows a previously verified immutable
representation within its freshness deadline.

## 9. Security evidence and runbooks

Deliver the runbooks required by the Design Freeze:

1. IdP outage and emergency login disable.
2. Session-store outage and recovery.
3. Mass revocation and security-epoch rotation.
4. Suspected session theft.
5. OIDC signing-key rotation.
6. Client-secret and KMS wrapping-key rotation.
7. OIDC configuration rollback.
8. Policy artifact rollback.
9. Capability Registry outage/staleness.
10. Audit ledger/archive outage.
11. Compromised Portal service identity.
12. Clock-skew response.
13. Emergency production-environment disable.
14. PostgreSQL restore with mandatory session invalidation.

Metrics must use bounded labels. Principal, subject, session, resource ID, correlation ID, raw URL,
and financial identifiers are prohibited metric labels.

## 10. Expected repository scope

Changes should remain limited to the security vertical slice, principally:

- `apps/portal-api/`
- `apps/portal-web/`
- `packages/portal-contracts/`
- a dedicated Portal security migration location;
- narrowly scoped local/CI Keycloak and Portal PostgreSQL fixtures;
- Portal security tests and operational runbooks;
- build/CI configuration strictly required to verify PR-PORTAL-002.

Do not include formatting-only refactors, unrelated dependency upgrades, platform pipeline
changes, speculative adapters, or domain feature code.

## 11. Stop conditions

Stop implementation and report the blocker when:

- `GD-001` is absent from the protected default branch, expired, suspended, or revoked;
- the protected default branch does not contain the merged and verified PR-PORTAL-001 foundation;
- a required change falls outside Section 3 of `GD-001`;
- a frozen invariant or ADR must change;
- a Source, CDC, Dataset, Pipeline, Recovery, DLQ, or infrastructure-administration API is needed;
- the implementation requires a browser token or client-authoritative authorization state;
- high-risk security state cannot be atomic with audit evidence;
- migrations cannot preserve compatibility and evidence;
- a real OIDC-provider, concurrency, or restore test would have to be bypassed;
- an unresolved P0/P1 security finding remains.

If a frozen ADR must change, do not patch around it. Stop, preserve evidence, mark the affected
design as requiring governance review, and wait for a superseding decision.

## 12. Definition of done

PR-PORTAL-002 is merge-ready only when:

1. All 25 Design Freeze merge gates and additional repository gates pass.
2. Every relevant threat-model row maps to a control and executable test.
3. Every failure-matrix row has the required HTTP, cookie/session, audit, retry, and alert
   behavior.
4. Generated authorization tests cover all frozen context dimensions and fail-closed unknowns.
5. Real Keycloak, PostgreSQL-role, browser, concurrency, outage, migration, and restore tests pass.
6. Browser artifacts and observability evidence contain no OIDC token or session secret.
7. The diff remains fully inside `GD-001`.
8. Focused security review has dispositioned every P0/P1 finding.
9. Required security, platform/database, CI, and maintainer approvals are recorded.
10. No document or UI claims production authorization.

Merge readiness does not authorize production deployment. A production pilot requires a later,
separate governance decision.

## 13. Required final report

Return:

1. Authorization preflight result, protected-default-branch base commit, and PR-PORTAL-001 merge
   evidence.
2. Implemented slices and exact files changed.
3. Migration versions, role grants, compatibility evidence, and rollback/roll-forward result.
4. OIDC/session/cookie/CSRF/security control summary.
5. Policy, capability, environment, tenant, and navigation enforcement evidence.
6. Audit ledger/outbox/archive integrity evidence.
7. OpenAPI/generated-client drift result.
8. Unit, integration, real-provider, browser, concurrency, recovery, migration, and regression test
   results.
9. Threat-model and failure-matrix coverage report.
10. Remaining risks and accepted-risk references.
11. Focused review findings by P0/P1/P2 and their disposition.
12. Final decision:
    - `MERGE READY`;
    - `NOT MERGE READY`;
    - never `PRODUCTION AUTHORIZED`.

Do not implement unrelated features, do not weaken a frozen failure mode to make tests pass, and
do not claim completion from mock-only or happy-path evidence.
