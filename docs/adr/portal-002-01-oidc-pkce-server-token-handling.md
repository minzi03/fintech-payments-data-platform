# ADR-PORTAL-002-01: OIDC Authorization Code, PKCE, and Server-Side Tokens

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

The Portal needs enterprise identity without making the browser a token holder or trusting an
identity-provider-specific SDK contract. Login must work with Keycloak, Authentik, Azure Entra ID,
and standards-compliant OIDC providers while failing closed under replay, provider outage, and
claim drift.

## Decision

Use OIDC Authorization Code Flow with mandatory PKCE S256. FastAPI owns login transactions, code
exchange, token validation, encrypted token material, principal mapping, refresh, and logout.

Issuers and redirect URIs are exact server configuration. State, nonce, verifier, browser binding,
provider, and return path live in a one-use five-minute server transaction. The browser receives
only redirects, safe identity/session views, and the opaque Portal session cookie.

Discovery/JWKS cache is 15 minutes with at most one hour stale use for previously trusted known
keys. Unknown signing keys require successful JWKS refresh. Production requires HTTPS and exact
issuer match. Claim documents, groups, roles, and value lengths are bounded.

## Alternatives

- Browser token handling: rejected because XSS, extensions, logs, and storage become token theft
  boundaries.
- Implicit or hybrid flow: rejected because front-channel tokens and weaker replay properties.
- Provider-specific authorization SDK: rejected because it couples policy to one IdP.
- Authentication proxy as the only authority: deferred; it would still need the same session,
  claim, revocation, and audit contract.

## Consequences

The Portal API becomes an identity availability dependency and must store encrypted refresh
material when issued. Local/CI needs a real OIDC container. Provider-specific claims require
explicit mapping, but product authorization remains provider-neutral.

## Security properties

- Tokens never enter frontend models, browser storage, URLs, logs, traces, or audit.
- State, nonce, PKCE, exact issuer/audience/authorized party, signature, and time are all required.
- Callback and refresh are one-use/serialized.
- Unknown provider, claim, key, algorithm, or transaction state fails closed.

## Failure behavior

Provider/discovery/token/JWKS failures return sanitized 401 or 503 according to whether the
credential is invalid or authority is unavailable. No session is created from an uncertain
callback. Refresh may serve R0 reads only inside the frozen identity-staleness bound.

## Migration

Add provider configuration, login-transaction persistence, encrypted token envelopes, new
OpenAPI endpoints, and generated client changes. PR-001 anonymous foundation routes become
session-protected except safe health/build contracts.

## Operational impact

Runbooks and alerts are required for IdP outage, key rotation, client-secret rotation, callback
failure, refresh replay, and emergency login disable.

## Testing

Unit tests cover parsing/mapping. Integration uses real Keycloak for discovery, PKCE, state, nonce,
callback, issuer/audience, replay, key rotation, refresh rotation, and logout. Browser tests prove
that no OIDC token is JavaScript-readable.

## Rollback

Disable login, revoke all Portal sessions using the security epoch, expire cookies, preserve
security audit, and deploy the previous compatible artifact. Never fall back to a production test
identity.
