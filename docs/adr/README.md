# Architecture Decision Record Registry

ADRs are production-readiness contracts, not implementation claims. A proposed ADR cannot
authorize runtime changes. A frozen ADR may be implemented only after the overall five-blocker
Design Freeze gate closes.

| ADR     | Decision                                | Backlog | Status                           | Review evidence                                          |
| ------- | --------------------------------------- | ------- | -------------------------------- | -------------------------------------------------------- |
| ADR-001 | Dataset bootstrap and atomic activation | PRD-001 | **Accepted, Frozen Revision 4**  | `docs/design-reviews/adr-001-design-freeze-review-v2.md` |
| ADR-002 | CDC key-only delete semantics           | PRD-002 | Proposed                         | Not started                                              |
| ADR-003 | Cross-system recovery checkpoints       | PRD-003 | Proposed                         | Not started                                              |
| ADR-004 | Runtime identity and storage IAM        | PRD-004 | Proposed                         | Not started                                              |
| ADR-005 | Versioned database migrations           | PRD-005 | Proposed                         | Not started                                              |
| ADR-006 | Governed DLQ redrive                    | PRD-019 | Proposed; post-blocker hardening | Not started                                              |
| ADR-007 | Append-only quality evaluations         | PRD-020 | Proposed; post-blocker hardening | Not started                                              |

## Governance

- Review one ADR at a time.
- Resolve every P0 and every unaccepted P1 before freeze.
- Record the frozen revision and SHA-256 externally in `docs/design-freeze.md`.
- Reopen the ADR when implementation needs to change a frozen invariant.
- Preserve prior review evidence; do not rewrite a rejected or revision-required verdict.
- Treat completion of ADR-001 through ADR-005 as permission to begin vertical implementation, not
  as production approval.

The authoritative phase gate is [`docs/design-freeze.md`](../design-freeze.md).

## Portal security Design Freeze

The Portal security ADRs below are frozen architecture contracts for PR-PORTAL-002. They do not
complete the repository-wide Design Freeze. The sole bounded implementation exception is
`docs/governance/decisions/GD-001-portal-002-implementation-exception.md`.

| ADR               | Decision                                                           | Status                          | Review evidence                              |
| ----------------- | ------------------------------------------------------------------ | ------------------------------- | -------------------------------------------- |
| ADR-PORTAL-002-01 | OIDC Authorization Code, PKCE, and server-held tokens              | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-02 | PostgreSQL server-session store and lifecycle                      | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-03 | Deny-by-default Portal authorization policy                        | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-04 | Capability Registry authority and precedence                       | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-05 | Request-scoped environment and single-organization tenant context  | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-06 | Transactional append-only security audit ledger and archive outbox | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |
| ADR-PORTAL-002-07 | Host-only session cookie and synchronizer CSRF strategy            | **Accepted, Frozen Revision 1** | `docs/portal/pr-portal-002-design-freeze.md` |

Supporting frozen contracts:

- `docs/portal/pr-portal-002-threat-model.md`
- `docs/portal/pr-portal-002-authorization-matrix.md`
- `docs/portal/pr-portal-002-failure-matrix.md`
