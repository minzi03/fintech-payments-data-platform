# ADR-PORTAL-002-04: Capability Registry Source of Truth and Precedence

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

The Portal must describe real product support without fabricating platform capabilities.
Availability, health, administrative enablement, and user authorization are different concerns.

## Decision

Capability definitions come from an immutable schema-validated deployment bundle. Revisioned
environment overlays provide administrative enablement. Native Portal implementation and
versioned adapter health provide observed evidence. A canonical digest forms the registry
revision.

Effective states are AVAILABLE, READ_ONLY, DEGRADED, PLANNED, and DISABLED. Precedence is:
invalid/unknown fail closed; administrative DISABLED; roadmap PLANNED; missing/incompatible
implementation PLANNED or DISABLED according to deployment expectation; stale/unhealthy required
dependency DEGRADED; configured read-only READ_ONLY; otherwise AVAILABLE.

Authorization filters visibility/actions after capability evaluation and never changes the
capability state.

## Alternatives

- Frontend feature flags: rejected because the browser is not authoritative.
- Health-only derivation: rejected because health cannot prove deployment support or
  administrative enablement.
- Policy and capability as one boolean: rejected because it hides whether denial is product
  availability or user authorization.

## Consequences

The registry requires versioned configuration and clear native/adapter contracts. PLANNED
capabilities remain absent from normal operational navigation.

## Security properties

- Unknown/stale authority cannot enable an operation.
- READ_ONLY is product mode, not a substitute for user policy.
- Cache is at most 30 seconds and revision-bound.
- Last verified reads may degrade; mutations always disable on stale authority.

## Failure behavior

Authority outage yields safe DEGRADED reads only where explicitly permitted and denies all
operations. Invalid definitions fail startup or disable the affected capability.

## Migration

Add capability schema/bundle, revisioned overlay persistence, evaluator, safe views, navigation
projection inputs, readiness metric, and current foundation capability definitions.

## Operational impact

Requires registry-age/revision monitoring, administrative disable and rollback runbooks, and
clear ownership/documentation/runbook links per capability.

## Testing

Test every precedence combination, freshness boundary, revision mismatch, native/adapter absence,
authorization separation, and frontend tampering.

## Rollback

Deploy the previous compatible definition bundle and overlays, increment registry revision, purge
projections, and keep unsupported capabilities disabled during uncertainty.
