# ADR-PORTAL-002-05: Environment and Tenant Context

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

A mutable session-wide environment races across tabs and can apply a decision to the wrong
environment. Adding a tenant column does not create real multi-tenancy.

## Decision

Choose request-scoped environment model B. Pages use an explicit environment route and scoped API
requests send `X-Portal-Environment-ID`. The server validates it against the current session,
tenant, environment registry, assurance, policy, capability, and resource on every request.

Environment selection is validated/audited by `POST /v1/session/environment` but does not mutate a
global session environment or roles. Multiple tabs can use different contexts safely.

PR-002 is single organization. Canonical tenant `fintech-platform-primary` comes from immutable
server configuration, not browser or arbitrary claim. Every session/resource/decision/audit record
contains it. Tenant mismatch fails closed and protected resource existence may be masked.

## Alternatives

- One selected environment per session: rejected because tabs/dialogs race.
- Separate session per environment: rejected because it multiplies token/session/revocation
  complexity for no current need.
- Browser-selected tenant: rejected because the product is not multi-tenant and the browser is
  untrusted.

## Consequences

Every environment-scoped query key and API call must carry explicit context. Deep links are stable
and safe. The UI cannot assume a global environment.

## Security properties

- Environment switch cannot grant entitlement or role.
- Production requires explicit entitlement and AAL2.
- Context/revision is pinned for future dialogs and operations.
- Missing/unknown/disabled environment or tenant mismatch fails closed.

## Failure behavior

Missing environment returns a validation error; inaccessible environment returns 403; protected
resource tenant/environment mismatch may return 404. Removed entitlement invalidates projections
on the next bounded identity refresh.

## Migration

Add environment/tenant identifiers to session views, API context, policy input, capability views,
query keys, navigation, and audit. PR-001 system dependency detail becomes authenticated and
scoped.

## Operational impact

Requires entitlement mapping governance, environment disable runbook, production-access alerts,
and cache invalidation across tabs.

## Testing

Test two tabs, header/query tampering, production escalation, missing/removed/disabled environment,
deep links, invalid tenant, same resource ID across environments, and stale entitlement.

## Rollback

Disable production environment first, invalidate environment projections, increment session epoch
if mappings are suspect, and deploy the previous compatible artifact without restoring anonymous
environment detail.
