# PORTAL-002 Workstream B Completion Report — F-002

## Report status

| Field | Value |
|---|---|
| Finding | F-002 |
| Workstream | B — OIDC provider authority |
| Implementation status | IMPLEMENTED |
| Engineering verification | IMPLEMENTATION-VERIFIED |
| Review readiness | READY FOR TARGETED RE-REVIEW |
| Independent review status | OPEN / NOT YET REVIEW-VERIFIED |
| Technical verdict | REQUEST CHANGES |
| Base immutable commit | `3b9d0fd32fac9237f7dbecab3ba78bf7338f8da3` |
| Repository state | Local uncommitted changes |
| Push / merge | None |
| Verification date | 2026-07-27 |

This report records engineering implementation evidence only. It does not close F-002,
accept Workstream B, authorize merge, authorize Milestone 2A, or authorize production.

## Authoritative F-002 baseline

The authoritative input was the accepted TRACEABLE REVIEW v2 ordered composite and the
approved Runtime Remediation Plan.

### Original finding record

**Finding ID:** F-002

**Title:** OIDC discovery, JWKS lifecycle, and client authority are incomplete

**Severity:** Major

**Affected Component:** OIDC provider integration

**Effective Governance Source:** ADR-PORTAL-002-01; Portal Design Freeze; Portal Threat Model

**Section:** ADR decision; Design Freeze §4; authorization-code interception threat control

**Normative Requirement:** Provider endpoints must derive from validated discovery; JWKS must
follow the frozen refresh and bounded-staleness lifecycle; the governed client-authentication
boundary must be enforced.

**Original Runtime Implementation Evidence:** Provider endpoints are statically configured;
JWKS entries are cached without the frozen refresh/staleness semantics; the local provider
configuration and token exchange do not demonstrate the governed confidential-client control.

**Impact:** Provider metadata changes or signing-key rotation can cause prolonged failure or
unsafe trust retention, and the client boundary may differ from the frozen security model.

**Original Recommended Remediation:** Implement validated discovery, governed JWKS
refresh/staleness behavior, unknown-key refresh handling, and the frozen client-authentication
boundary without exposing credentials to the browser.

**Merge Disposition:** MUST FIX BEFORE MERGE

**Disposition Owner:** Repository maintainer with security-review verification

**Original Finding Status:** OPEN

**Original Verification Evidence:** No discovery, signing-key rotation, stale-key, unknown-key,
or client-boundary integration evidence was identified.

**Related Findings:** F-010, F-011

### Approved F-002 disposition wording

The approved disposition established that the implementation already performed one forced JWKS
refresh after an unknown `kid` and failed closed when the key remained unavailable. That behavior
was retained.

The approved remaining remediation was:

- validated OIDC discovery;
- governed JWKS TTL and stale-ceiling semantics;
- explicit provider-outage and stale-cache behavior;
- successful signing-key rotation integration coverage;
- the frozen confidential-client authentication boundary.

### Authoritative pre-remediation evidence locators

Reviewed commit:

```text
660889b97698c93f0febeb6a01136bf8b5cd626a
```

Original implementation locators:

```text
apps/portal-api/app/portal_api/core/config.py
PortalApiSettings
Lines 58–70

apps/portal-api/app/portal_api/auth/provider_config.py
OidcProviderConfig.from_settings()
Lines 21–31

apps/portal-api/app/portal_api/auth/oidc_provider.py
HttpxOidcProvider.exchange_code()
Lines 27–84

apps/portal-api/app/portal_api/auth/oidc_provider.py
HttpxOidcProvider.get_jwks()
Lines 86–103

apps/portal-api/app/portal_api/auth/token_validation.py
PyJwtTokenValidator._resolve_key()
Lines 127–140

infrastructure/keycloak/realm-export.json
Keycloak fintech-portal client configuration
Lines 47–52
```

### Approved required outcome

OIDC provider authority comes from validated discovery. JWKS freshness and staleness follow the
governed lifecycle, and the client authenticates according to the frozen confidential-client
boundary.

The existing one-time forced refresh for an unknown `kid` remains intact.

### Approved acceptance criteria

1. Discovery metadata is retrieved and validated against the configured issuer.
2. Authorization, token and JWKS endpoints derive from validated metadata.
3. Untrusted or inconsistent discovery metadata fails closed.
4. Ordinary JWKS reuse obeys the governed TTL.
5. Stale JWKS is never trusted beyond the governed ceiling.
6. Unknown `kid` causes one forced refresh.
7. A legitimate rotated signing key succeeds after refresh.
8. An unresolved unknown key fails closed.
9. Provider outage behavior is deterministic for fresh, stale-within-policy and
   stale-beyond-policy cache states.
10. Token exchange enforces confidential-client authentication.
11. Browser-visible artifacts contain no client credentials or provider tokens.

### Approved verification expectations

- **Unit tests:** Discovery validation, cache age, stale ceiling and unknown-key transitions.
- **Integration tests:** Successful signing-key rotation and provider outage.
- **Security validation:** Issuer substitution, malicious endpoint metadata and missing/invalid
  client authentication.
- **Manual verification:** Local identity-provider rotation exercise may supplement automated
  integration evidence.

## Existing gap assessment

Before this remediation:

- `OidcProviderConfig.from_settings()` copied authorization, token, and JWKS endpoints directly
  from static settings.
- `HttpxOidcProvider` retained JWKS for the life of the process without cache age, a 15-minute
  freshness TTL, or a one-hour stale ceiling.
- `PyJwtTokenValidator` already performed exactly one forced refresh for an unknown signing key.
- token exchange sent only a public `client_id` and did not authenticate the server-side client;
- the local Keycloak realm declared the Portal client as public;
- login initiation constructed its redirect from static provider configuration.

No migration was required. The API surface and browser contract remain unchanged.

## Implementation summary

### Validated discovery authority

- Added an asynchronous discovery boundary to `OidcProviderPort`.
- Replaced static endpoint construction with `OidcProviderConfig.from_discovery()`.
- Require exact configured-issuer equality.
- Require absolute HTTP(S) authorization, token, and JWKS endpoints without embedded credentials
  or fragments.
- Require discovered endpoints to remain on the configured issuer origin.
- Require discovery metadata to advertise `client_secret_basic`.
- Resolve the login authorization endpoint and callback exchange/JWKS endpoints through the same
  validated provider authority.

### Governed discovery and JWKS cache lifecycle

- Added monotonic-age discovery and JWKS cache records.
- Fixed the governed freshness TTL at 900 seconds.
- Fixed the governed stale ceiling at 3,600 seconds.
- Reuse fresh material without network access.
- Permit stale material only after an ambiguous provider outage and only within the ceiling.
- Reject invalid or inconsistent metadata as authoritative failure without stale fallback.
- Never use stale JWKS during the unknown-key forced-refresh path.
- Invalidate cached JWKS if validated discovery changes the JWKS URI.
- Retained one ordinary lookup plus one forced refresh for unknown `kid`.

### Confidential-client boundary

- Added server-only `PORTAL_API_OIDC_CLIENT_SECRET`.
- Require the secret whenever the security runtime is enabled.
- Authenticate token exchange with HTTP Basic client authentication.
- Omit both `client_id` and `client_secret` from the form body.
- Converted the local Keycloak Portal client from public to confidential.
- Kept credentials and provider tokens behind the FastAPI/BFF boundary.

### Local identity-provider authority

- Unified browser and container issuer authority on
  `http://portal-idp.localhost:8081/realms/fintech-portal`.
- Added the corresponding Keycloak network alias and public hostname.
- Kept the browser redirect URI unchanged.
- Updated the Portal Web build/runtime default identity-provider URL.

## Acceptance criteria matrix

| # | Acceptance criterion | Status | Implementation and verification evidence |
|---|---|---|---|
| 1 | Discovery retrieved and exact issuer validated | Satisfied | `OidcProviderConfig.from_discovery()` and `test_validated_discovery_is_the_only_endpoint_authority`; live Keycloak discovery returned the exact configured issuer. |
| 2 | Endpoints derive from validated metadata | Satisfied | Login redirect, token exchange, and JWKS use `OidcProviderPort.get_config()`; live host and container discovery returned the used endpoints. |
| 3 | Untrusted or inconsistent discovery fails closed | Satisfied | Parameterized tests reject issuer substitution, cross-origin token endpoint, and missing governed auth method with `AUTHORITATIVE_REJECTION`. |
| 4 | Ordinary JWKS reuse obeys 15-minute TTL | Satisfied | Time-controlled adapter test verifies no additional provider request while cache age is 100 seconds and refresh behavior after 901 seconds. |
| 5 | Stale JWKS never exceeds one hour | Satisfied | Time-controlled outage test accepts bounded stale material at 901 seconds and fails at 3,601 seconds. |
| 6 | Unknown `kid` causes one forced refresh | Satisfied | Existing token-validator unit evidence remains green; adapter integration observes exactly two JWKS requests: initial and one forced refresh. |
| 7 | Legitimate rotated key succeeds after refresh | Satisfied | Real HTTP-adapter path with `httpx.MockTransport` rotates from `signing-v1` to `signing-v2`; token validation succeeds after the forced refresh. |
| 8 | Unresolved unknown key fails closed | Satisfied | Integration test retains only an unrelated key through refresh and raises `TokenValidationError`. |
| 9 | Outage behavior is deterministic across cache states | Satisfied | Time-controlled test covers fresh reuse, stale-within-policy fallback, forced-refresh failure, and stale-beyond-policy failure. |
| 10 | Token exchange enforces confidential-client auth | Satisfied | Adapter test asserts the exact Basic Authorization header and absence of client credentials in the body; live Keycloak E2E login succeeds with a confidential client. |
| 11 | Browser artifacts contain no client credentials/provider tokens | Satisfied | Real OIDC Playwright test verifies only the opaque HttpOnly Portal session boundary; replay and CSRF tests fail closed. |

## Evidence locators

### Runtime implementation

- `apps/portal-api/app/portal_api/auth/provider_config.py`
  - `OidcProviderConfig.from_discovery()`
  - `_validated_provider_endpoint()`
- `apps/portal-api/app/portal_api/auth/oidc_provider.py`
  - `HttpxOidcProvider.get_config()`
  - `HttpxOidcProvider.get_jwks()`
  - `HttpxOidcProvider.exchange_code()`
  - `HttpxOidcProvider._get_json()`
- `apps/portal-api/app/portal_api/auth/login_intent.py`
  - `LoginInitiationService.start_login()`
  - `LoginInitiationService._authorization_url()`
- `apps/portal-api/app/portal_api/core/config.py`
  - `PortalApiSettings.oidc_discovery_url_value`
  - `PortalApiSettings.oidc_client_secret`
  - `PortalApiSettings.oidc_cache_ttl_seconds`
  - `PortalApiSettings.oidc_stale_ceiling_seconds`
- `apps/portal-api/app/portal_api/main.py`
  - `create_app()`

### Provider and local runtime configuration

- `.env.example`
- `docker-compose.yml`
- `infrastructure/keycloak/realm-export.json`
- `apps/portal-web/Dockerfile`
- `apps/portal-web/next.config.ts`

### Focused verification

- `apps/portal-api/tests/integration/test_oidc_provider_lifecycle.py`
  - `test_validated_discovery_is_the_only_endpoint_authority()`
  - `test_untrusted_discovery_metadata_fails_closed()`
  - `test_jwks_outage_policy_is_fresh_then_bounded_stale_then_fail_closed()`
  - `test_legitimate_rotated_signing_key_succeeds_after_one_forced_refresh()`
  - `test_unresolved_unknown_key_still_fails_closed_after_forced_refresh()`
  - `test_token_exchange_uses_confidential_basic_auth_without_body_secret()`
  - `test_invalid_confidential_client_authentication_fails_closed()`
- `apps/portal-api/tests/unit/test_config.py`
- `apps/portal-api/tests/unit/test_token_validation.py`
- `apps/portal-web/tests/e2e/security/authentication.spec.ts`
- `apps/portal-web/tests/e2e/security/replay-and-csrf.spec.ts`

### Regression compatibility

- `apps/portal-api/tests/integration/test_login_initiation.py`
- `apps/portal-api/tests/integration/test_callback_orchestration.py`
- `scripts/portal/f003_restore_evidence.py`

The F-003 harness changes only adapt its controlled provider and test settings to the new provider
port and confidential-client configuration. They do not change F-003 runtime semantics or its
frozen historical evidence.

## Changed-file manifest

### Runtime and API

- `.env.example`
- `apps/portal-api/app/portal_api/api/v1/auth.py`
- `apps/portal-api/app/portal_api/auth/callback.py`
- `apps/portal-api/app/portal_api/auth/login_intent.py`
- `apps/portal-api/app/portal_api/auth/oidc_provider.py`
- `apps/portal-api/app/portal_api/auth/ports.py`
- `apps/portal-api/app/portal_api/auth/provider_config.py`
- `apps/portal-api/app/portal_api/core/config.py`
- `apps/portal-api/app/portal_api/main.py`
- `apps/portal-web/Dockerfile`
- `apps/portal-web/next.config.ts`
- `docker-compose.yml`
- `infrastructure/keycloak/realm-export.json`

### Tests and compatibility evidence

- `apps/portal-api/tests/integration/test_callback_orchestration.py`
- `apps/portal-api/tests/integration/test_login_initiation.py`
- `apps/portal-api/tests/integration/test_oidc_provider_lifecycle.py`
- `apps/portal-api/tests/unit/test_config.py`
- `apps/portal-api/tests/unit/test_token_validation.py`
- `scripts/portal/f003_restore_evidence.py`

### Completion evidence

- `docs/governance/reviews/PORTAL-002-workstream-b-completion-report-f002.md`

## Verification results

### Backend functional and regression suite

Start: `2026-07-27T16:23:59.7387154Z`

```text
python -m pytest apps/portal-api/tests/unit \
  apps/portal-api/tests/integration/test_login_initiation.py \
  apps/portal-api/tests/integration/test_callback_orchestration.py \
  apps/portal-api/tests/integration/test_oidc_provider_lifecycle.py \
  -q -p no:cacheprovider
```

Result: **PASS — 128 passed, 0 failed, 0 skipped, 0 deselected**.

Warning: one existing Starlette deprecation warning concerning `httpx` and `TestClient`.

### Backend lint and formatting

Start: `2026-07-27T16:18:01.7821674Z`

```text
python -m ruff check --no-cache apps/portal-api/app apps/portal-api/tests \
  scripts/portal/f003_restore_evidence.py
python -m ruff format --no-cache --check apps/portal-api/app apps/portal-api/tests \
  scripts/portal/f003_restore_evidence.py
```

Result: **PASS — all lint checks passed; 77 files formatted**.

### Backend static typing

Start: `2026-07-27T16:29:37.5380691Z`

```text
python -m mypy --no-incremental --cache-dir .tmp-mypy-f002 \
  --config-file apps/portal-api/pyproject.toml apps/portal-api/app/portal_api
```

Result: **PASS — no issues in 57 source files**. The bounded temporary cache was removed after
execution.

### Frontend checks

```text
2026-07-27T16:16:49.6355446Z  pnpm --filter @fintech/portal-web lint
2026-07-27T16:17:33.8736432Z  pnpm --filter @fintech/portal-web typecheck
2026-07-27T16:17:40.1673533Z  pnpm --filter @fintech/portal-web test
2026-07-27T16:17:52.5197825Z  pnpm --filter @fintech/portal-web format:check
```

Result: **PASS**.

- ESLint: 0 warnings.
- TypeScript: no errors.
- Vitest: 11 files and 25 tests passed; 0 skipped.
- Prettier: all checked files matched.

### Real Keycloak and browser verification

The tracked stack was rebuilt with Keycloak 26.4.7 and a transient
`PORTAL_IDP_PUBLIC_URL=http://portal-idp.localhost:8081` override because the user-owned local
`.env` remains intentionally unmodified.

Verified from both the host and the `portal-api` container:

- exact issuer:
  `http://portal-idp.localhost:8081/realms/fintech-portal`;
- authorization, token, and JWKS endpoints from discovery;
- `client_secret_basic` advertised by the provider;
- Keycloak, Portal API, and Portal Web healthy.

Playwright start: `2026-07-27T16:18:23.1295483Z`

```text
PORTAL_E2E_AUTH=1
PORTAL_E2E_EXTERNAL=1
PORTAL_WEB_URL=http://localhost:3000
pnpm --filter @fintech/portal-web run e2e --output=.tmp-playwright-f002
```

Result: **PASS — 3 passed, 0 failed, 0 skipped**.

- BFF foundation connected.
- Real OIDC login created only an opaque HttpOnly Portal session.
- Callback replay and mutation without CSRF failed closed.

The temporary Playwright output directory was removed after execution.

### Compose and repository checks

Start: `2026-07-27T16:18:07.6982541Z`

```text
PORTAL_IDP_PUBLIC_URL=http://portal-idp.localhost:8081
docker compose --env-file .env.example config --quiet
```

Result: **PASS**. Docker emitted only a local client-config permission warning and returned zero.

`git diff --check` passed after implementation.

## Regression assessment

- F-001 revocation, rotation, concurrent logout, and callback fencing tests remain green in the
  callback regression suite.
- F-003 restart, durable-key transition, unknown-key, and restart compatibility tests remain
  green in the callback regression suite.
- The real browser login, callback replay, CSRF, opaque-cookie, and BFF boundary tests remain
  green.
- No migration or data-shape change was introduced.
- No browser interface was added for provider credentials or provider tokens.
- No F-001 or F-003 finding, review status, or historical artifact was modified.

## Known limitations and remaining gaps

No F-002 implementation or verification gap is known at completion time.

Operational notes that do not change the F-002 completion decision:

- this is local/development evidence, not production readiness evidence;
- no CI run is claimed; all recorded verification is local;
- the user-owned local `.env` still contains an older identity-provider URL and was not modified;
  the tracked `.env.example` and Compose defaults contain the remediated authority, and the final
  live verification used an explicit transient override;
- the existing Starlette `TestClient` deprecation warning remains unrelated to F-002;
- independent review has not yet occurred.

## Completion decision

**COMPLETED**

F-002 is:

```text
IMPLEMENTED
IMPLEMENTATION-VERIFIED
READY FOR TARGETED RE-REVIEW
OPEN
NOT YET REVIEW-VERIFIED
```

Workstream B implementation is complete but remains unaccepted pending independent review.

## Updated technical checkpoint

```text
Technical verdict:             REQUEST CHANGES
F-001:                         REVIEW-VERIFIED / CLOSED
F-003:                         REVIEW-VERIFIED / CLOSED
Workstream A review:           ACCEPTED WITH OBSERVATIONS
F-002 implementation:         IMPLEMENTED
                               IMPLEMENTATION-VERIFIED
                               READY FOR TARGETED RE-REVIEW
F-002 review:                 OPEN
                               NOT YET REVIEW-VERIFIED
Workstream B implementation:  COMPLETED
Workstream B review:          NOT STARTED
PR #11:                       OPEN
Milestone 2A:                 NOT AUTHORIZED
Production:                   NOT GRANTED
Repository:                   LOCAL UNCOMMITTED CHANGES
Push / merge:                 NONE
```

## Next authorized step

**Review Target Freeze — Workstream B**

## Repository statement

This activity:

- implemented only the authorized F-002 remediation;
- did not modify F-001 or F-003 findings or review decisions;
- performed no independent technical review;
- performed no finding closure;
- performed no staging or commit;
- performed no push;
- performed no merge;
- granted no Milestone 2A authorization;
- granted no production authorization.
