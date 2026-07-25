# GC-PORTAL-002-01 — OIDC Implementation Sequencing Clarification

- Decision ID: `GC-PORTAL-002-01`
- Status: **PROPOSED**
- Decision date: 2026-07-26
- Authority: repository governance authority
- Scope owner: Portal security implementation
- Production deployment authorization: **NOT GRANTED**

## 1. Context

Commit `a8d3842` on branch `codex/pr-portal-002-security-boundary` attempted
Slice 02 OIDC PKCE implementation. The Slice 02A conformance review (decision C
— BLOCKED) identified eight governance blockers where the implementation
diverged from frozen contracts or where frozen contracts did not provide
sufficient specification.

This governance clarification documents the blockers, determines which require
new ADRs or design freeze amendments, and identifies what may proceed
independently.

This document does NOT make architectural decisions. Each blocker that requires
a new design decision must be resolved through a separate ADR or design freeze
amendment before runtime implementation may begin.

## 2. Blockers

### BLOCKER 1 — Versioned Database Migrations

**Finding:** portal-api has no migration mechanism. The frozen design requires
PostgreSQL `portal_control` with 11 tables including `oidc_login_transactions`,
`portal_sessions`, `security_audit_events`, and `schema_migrations`.

**Status:** The frozen design freeze (section 16) defines the schema but does not
specify the migration tool, directory structure, numbering, or CI workflow.

**Required governance action:** New ADR for migration mechanism selection.

**Scope of the ADR:** Tool choice, directory layout, numbering convention,
forward/downgrade policy, startup behavior, CI validation, database ownership.

**This ADR is NOT frozen by this document.** The migration tool is an open
design question.

### BLOCKER 2 — PKCE Verifier Encryption

**Finding:** The frozen design (section 4) requires the PKCE verifier to be
"envelope-encrypted in the transaction store." The implementation stores it in
plaintext.

**Status:** The frozen design specifies AES-256-GCM and KMS-wrapped data keys
for provider token envelopes (section 4, "Token handling"). It does not explicitly
state whether the PKCE verifier uses the same envelope abstraction or a separate
mechanism.

**Required governance action:** Design freeze amendment or ADR clarifying:
- Whether PKCE verifier encryption reuses the token-envelope abstraction
- Key hierarchy for verifier-specific encryption
- Whether local/test ephemeral key behavior is acceptable

**This decision is NOT frozen by this document.**

### BLOCKER 3 — Browser Binding

**Finding:** The frozen design (section 4) requires a "browser-binding hash" in
the login transaction and validation at callback (step 2). It does not define:
- What signals constitute the binding (cookie, IP, User-Agent, etc.)
- The hash/HMAC algorithm
- Whether IP is included or excluded
- The callback validation procedure

**Status:** This is an open design question. The frozen documents require the
binding to exist but do not specify its construction.

**Required governance action:** New ADR for browser binding contract.

**Scope of the ADR:** Binding signal selection, hash algorithm, storage
representation, callback validation, privacy implications, cross-tab behavior.

**This ADR is NOT frozen by this document.**

### BLOCKER 4 — Login Intent

**Finding:** The frozen design (section 15) defines `GET /v1/auth/login-context`
returning a "Short-lived login-intent view" and `POST /v1/auth/login` requiring
"Login-intent + Origin." It does not specify:
- The `LoginContextView` response schema
- The login-intent token format, storage, or transport
- Whether a new database table is required
- The exact TTL

**Status:** The endpoint contract is frozen (method, auth, CSRF, result) but the
request/response shapes are not.

**Required governance action:** Design freeze amendment for `LoginContextView`
schema and login-intent contract.

**This amendment is NOT frozen by this document.**

### BLOCKER 5 — Outbound OIDC HTTP Policy

**Finding:** The frozen design specifies timeouts for JWKS cache (15 min TTL, 1
hr stale ceiling) and clock skew (60 sec max). It does not specify:
- Discovery/JWKS/token-exchange HTTP timeouts
- Maximum response sizes
- Redirect behavior
- Retry policy

**Status:** These are implementation details that do not alter trust, replay,
authority, or privacy semantics — provided they are bounded and fail-closed.

**Required governance action:** May be resolved as non-security implementation
details IF the chosen values are bounded, conservative, and documented. No ADR
required unless the values affect security properties.

**This is the only blocker that MAY proceed without a new ADR**, subject to
review approval.

### BLOCKER 6 — Callback Completion Boundary

**Finding:** The frozen callback contract (section 4, steps 1-15) includes
session creation (step 12), audit evidence (step 12), and transaction consumption
(step 14) as part of the callback flow. The Slice 02 scope prohibited session
and audit implementation. This creates a sequencing contradiction.

**Status:** The frozen implementation order (section 25) lists the implementation
sequence but does not define whether the callback endpoint may be exposed before
session/audit components exist.

**Required governance action:** Decision on callback sequencing model:
- Model A: Callback completed in one integrated slice
- Model B: Callback route exists but returns safe not-ready until complete
- Model C: Callback delegates to an orchestration port

**This decision is NOT frozen by this document.** All three models are valid
under the frozen contracts.

### BLOCKER 7 — Atomicity

**Finding:** The frozen design (section 4, step 12) mandates "Create the new
session and audit evidence atomically." The exact database transaction boundary
is not specified.

**Status:** The atomicity requirement is frozen. The exact records and commit
boundary are implementation details that depend on the callback model chosen in
Blocker 6.

**Required governance action:** Resolved after Blocker 6 is decided. No separate
ADR needed — the atomicity contract follows from the callback model.

### BLOCKER 8 — Slice Ownership

**Finding:** The original 12-slice implementation sequence does not assign clear
ownership for migration foundation, browser binding, login intent, verifier
encryption, or callback orchestration.

**Status:** The implementation order is frozen (section 25) but the slice
definitions in the implementation prompt are not. Slice boundaries may be
redefined without a governance change, provided no frozen contract is violated.

**Required governance action:** Revised slice definitions. This is an
implementation planning decision, not a governance decision. No ADR required.

## 3. Required Governance Actions

| Blocker | Action Required | Mechanism | Frozen? |
|---|---|---|---|
| 1. Migration mechanism | New ADR | ADR-PORTAL-002-08 (proposed) | NO |
| 2. PKCE verifier encryption | Clarify envelope reuse | Design freeze amendment | NO |
| 3. Browser binding | New ADR | ADR-PORTAL-002-09 (proposed) | NO |
| 4. Login intent | Schema freeze | Design freeze amendment | NO |
| 5. HTTP policy | Document values | Implementation detail (no ADR) | N/A |
| 6. Callback model | Choose A/B/C | Governance decision | NO |
| 7. Atomicity | Follows from #6 | Implementation detail | N/A |
| 8. Slice definitions | Revise plan | Implementation planning | N/A |

## 4. What May Proceed Without New ADRs

The following work does NOT require new governance decisions:

- PKCE utility (already correct)
- Provider metadata parsing (already correct)
- JWKS cache with frozen TTL (already correct)
- OIDC configuration fields (already correct)
- Error codes (already correct)
- Unit test patterns for existing components
- HTTP client setup with bounded, conservative defaults (Blocker 5)
- Removing unused dependencies (sqlalchemy, psycopg)

## 5. What Requires New Governance Before Implementation

The following CANNOT proceed until the corresponding governance action is
complete:

- PostgreSQL migration setup (Blocker 1)
- Login transaction persistence (Blocker 1)
- PKCE verifier encryption (Blocker 2)
- Browser binding implementation (Blocker 3)
- Login intent schema and storage (Blocker 4)
- Callback endpoint exposing success (Blocker 6)
- Session creation in callback (Blocker 6)
- Audit evidence in callback (Blocker 6)

## 6. Commit a8d3842 Disposition

Commit `a8d3842` contains both conformant and non-conformant components.

**Conformant components (may remain):**
- `auth/pkce.py` — PKCE utility
- `auth/models.py` — Data models
- `auth/provider.py` — Provider structure (interface only, persistence deferred)
- `core/errors.py` — OIDC error codes
- `core/config.py` — OIDC configuration fields
- `tests/unit/auth/test_pkce.py` — PKCE tests
- `tests/unit/auth/test_config.py` — Config tests

**Non-conformant components (must be replaced or disabled):**
- `auth/transactions.py` — In-memory store (not authoritative)
- `auth/service.py` — Static browser binding, plaintext verifier
- `auth/router.py` — X-Portal-Identity header, callback without orchestration
- `tests/integration/test_auth_api.py` — Mocked provider (not fake-IdP)
- `tests/unit/auth/test_service.py` — Tests against in-memory store

**Recommended disposition:** Keep commit as-is for now. After governance
decisions are made, create a remediation commit that replaces non-conformant
components. Do not amend or revert `a8d3842`.

## 7. Consequences

- Implementation is paused pending the governance actions identified in Section 9
- The 12-slice plan may be revised without governance change
- No frozen contracts are modified by this clarification
- No runtime implementation is authorized by this document

## 8. Approval

**Required approvers:**
- Repository governance authority

**Governance status:** PROPOSED

**Ready for governance PR:** YES

**Runtime implementation authorized:** NO — pending Blockers 1, 3, and 6

## 9. Required Governance Actions Before Runtime Continues

The following governance actions must be completed before the affected
runtime implementation continues:

1. Migration mechanism ADR (Blocker 1)
2. PKCE verifier encryption clarification (Blocker 2)
3. Browser-binding ADR (Blocker 3)
4. Login-intent contract amendment (Blocker 4)
5. Callback sequencing decision — Model A, B, or C (Blocker 6)

## 10. Next Action

1. Review and approve GC-PORTAL-002-01 as a clarification (not a design).
2. Merge governance PR (#5) into main.
3. For each blocker requiring a new ADR, create a separate ADR proposal.
4. Do NOT begin runtime remediation until required ADRs are frozen.
5. The OIDC library layer (PKCE, provider, JWKS) may proceed independently.
