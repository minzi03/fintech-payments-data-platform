# ADR-PORTAL-002-07: Cookie and CSRF Security Strategy

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

Server-side sessions require a browser credential, while all present and future unsafe operations
must resist CSRF, login CSRF, same-site subdomain attacks, and cookie fixation.

## Decision

Production uses one opaque `__Host-fintech_portal_session_v1` cookie with HttpOnly, Secure,
SameSite=Lax, Path=/, and no Domain. Local loopback has an explicitly validated non-Secure
exception and a non-`__Host-` name.

Use a server-session synchronizer CSRF token. The browser receives it in a no-store response,
holds it in memory, and sends `X-CSRF-Token` for every unsafe method. Server validates token,
session/version binding, and exact Origin; a validated Referer is a limited fallback.

Login starts with same-origin POST plus a short-lived browser-bound login intent. Logout is POST
and requires CSRF. OIDC callback GET is the only documented side-effecting safe-method exception
and requires state, nonce, PKCE, one-use transaction, and browser binding.

## Alternatives

- JWT or roles in cookie: rejected because browser credentials become authorization state.
- Double-submit cookie: rejected because a server session already supports stronger synchronizer
  binding.
- SameSite alone: rejected because it is defense in depth, not a complete CSRF control.
- SameSite=None form-post callback: rejected initially because it broadens cookie cross-site
  delivery and browser compatibility risk.

## Consequences

Frontend API wrapper must fetch/rotate the CSRF token and cannot persist it. Every future mutation
inherits this enforcement. Exact-origin deployment and reverse-proxy configuration become
security-critical.

## Security properties

- JavaScript cannot read the session cookie.
- Subdomains cannot set/send the host-prefixed production cookie for the Portal host.
- CSRF token from another, stale, or rotated session fails.
- CSRF never substitutes for authorization.
- Cookie contains no token, role, environment, capability, or profile.

## Failure behavior

Missing/mismatched token or Origin returns 403 Problem Details, keeps the session unless risk
policy revokes it, and appends `auth.csrf_rejected.v1`. Invalid/revoked cookies return 401 and are
expired.

## Migration

Add cookie/security configuration, startup checks, session CSRF generation, login-intent
endpoint/record, enforcement middleware/dependency, no-store headers, and frontend in-memory
handling.

## Operational impact

Requires TLS/proxy trust validation, cookie-version migration, emergency epoch rotation, Origin
rejection monitoring, and cross-subdomain deployment review.

## Testing

Inspect cookie flags and browser storage; test missing/foreign/stale token, malicious/missing
Origin/Referer, login/logout CSRF, subdomain attack, CORS preflight, callback binding, fixation,
rotation, and deletion.

## Rollback

Disable login/mutations, revoke sessions, expire every recognized cookie version, preserve audit,
and deploy the previous compatible security configuration. Never relax CSRF in production to
restore availability.
