# ADR-PORTAL-002-03: Portal Authorization Policy Model

- Status: **Accepted — Frozen Revision 1**
- Date: 2026-07-24
- Review evidence: `docs/portal/pr-portal-002-design-freeze.md`

## Context

Roles alone cannot protect environment, tenant, capability, resource, classification, purpose,
assurance, and future operation-risk boundaries. Client-side navigation is untrusted.

## Decision

Use a pure in-process, deny-by-default policy module with a schema-validated immutable policy
bundle packaged in the Portal API artifact. The canonical bundle digest is the policy revision.

Decisions evaluate normalized principal attributes and stable IDs across role, group, tenant,
environment, domain, capability, resource, action, classification, purpose, assurance, and
revision. Values are ALLOW, DENY, NOT_APPLICABLE, and INDETERMINATE; only ALLOW grants access.

FastAPI is the policy enforcement point. Next.js consumes safe projection hints only. Roles are
non-hierarchical and all grants are explicit in the frozen authorization matrix. PR-002 grants no
production mutation.

## Alternatives

- Role checks in routes/components: rejected because they cannot express contextual policy and
  drift across layers.
- OPA now: deferred because the initial read-only policy does not justify another failure domain.
- IdP authorization services: rejected as product policy authority because resource and
  capability context belongs to the Portal.

## Consequences

Policy deploys with application artifacts and must be reviewed, simulated, versioned, and rolled
back as code. Interfaces remain engine-neutral for later OPA migration.

## Security properties

- Unknown action/resource/revision is INDETERMINATE and fails closed.
- ALLOW cache keys include exact session, tenant, environment, resource, policy, and capability
  revisions and expire within 30 seconds.
- Production access requires explicit entitlement and AAL2.
- Resource existence may be masked before retrieval according to resource class.

## Failure behavior

Missing/invalid policy prevents protected operation and returns 503. A role or capability does not
override a failed context gate. A stale ALLOW is never used across a revision.

## Migration

Add typed policy inputs/decisions, stable action registry, revision exposure, enforcement
dependencies, navigation hints, Problem Details codes, and generated matrix tests.

## Operational impact

Requires policy artifact provenance, simulation report, revision metric, rollback procedure, and
denial/indeterminate monitoring without high-cardinality labels.

## Testing

Generate parameterized tests from the frozen matrix, including unknowns, tenant mismatch,
production entitlement/AAL, capability states, revision changes, and direct API access.

## Rollback

Deploy the previous signed policy/application artifact, invalidate decision/navigation caches, and
audit the rollback. Never retain decisions from the rolled-back revision.
