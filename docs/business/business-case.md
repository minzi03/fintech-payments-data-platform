# Business Case

## Business context

The project models a hypothetical mid-sized fintech providing payment gateway integration, merchant
payments, account-to-account transfers, refunds, and partner-bank settlement. Internal users include
Payment Operations, Finance, Risk, Product, Customer Support, Compliance, Analytics, and Data
Engineering.

Phase 1 supplies a constrained OLTP source and repeatable synthetic payment data. Phase 2 adds a
versioned, replay-safe ingestion boundary for banking-partner settlement files. Phase 3 makes raw
and quarantine persistence selectable between local files and private MinIO without changing the
business rules. Reconciliation and business dashboards remain unimplemented.

## Implemented source coverage

- Merchant payments through card, bank transfer, QR, or wallet channels.
- Account-to-account transfers between customer accounts.
- Successful, failed, and pending payment lifecycles.
- Full and partial refunds linked to completed transactions.
- External partner references needed by future settlement matching.

- Daily banking-partner settlement CSV files with valid, mismatch-candidate, duplicate, and invalid
  scenarios.

## Current problems addressed

- Engineers need realistic related data before building CDC and analytics pipelines.
- Payment current state and immutable status history need explicit, testable semantics.
- Duplicate requests must be rejected through an idempotency key.
- Invalid money, currencies, statuses, and relationships must fail at the source boundary.
- Test scenarios must be reproducible by seed without storing sensitive customer identity data.

## Long-term use cases

### Near-real-time payment operations monitoring

Operations will monitor volume, success/failure rates, latency, refunds, channel health, and pending
backlog. Phase 1 provides the current transaction row and immutable events needed by a future CDC and
event pipeline; it does not provide a near-real-time dashboard.

### Daily settlement reconciliation

Finance will compare completed internal transactions with partner settlement lines and classify
matches, missing items, duplicates, amount/currency mismatches, and status mismatches. Phase 2 now
validates the evidence; Phase 3 preserves it in immutable object storage with checksum/manifest
lineage. Matching logic is still deliberately unimplemented.

## Stakeholders

| Stakeholder | Need | Phase 1 contribution |
| --- | --- | --- |
| Payment Operations | Status and failure visibility | Reproducible lifecycle source data |
| Finance | Auditable settlement matching | Fixed-precision partner evidence and replay-safe manifest |
| Product/Risk | Consistent domain behavior | Validated transaction types, channels, and statuses |
| Customer Support | Payment/refund lookup | Related current-state records without sensitive identity data |
| Data Engineering | Stable source contracts | Versioned SQL/CSV contracts, generators, tests, and runbooks |
| Compliance | Minimal data exposure | No national ID, card data, or real credentials |

## Expected value

Phases 1-3 reduce downstream ambiguity by establishing source grain, lifecycle rules, precision,
content identity, quarantine evidence, deterministic fixtures, and backend-neutral raw storage.
Business outcomes remain targets until later data products are measured.

## Assumptions and validation

| Assumption | Phase 1 status | Later validation |
| --- | --- | --- |
| Payments expose current state plus immutable lifecycle events. | Modeled and tested locally | CDC/event pipeline replay tests |
| A customer may own multiple single-currency accounts. | Modeled; overdraft excluded | Product policy review |
| Merchant payments and account transfers cover the first source slice. | Implemented generator scope | Contract review before CDC |
| Partner references are unique when present. | Database constraint | Settlement partner contract review |
| Partners can provide UTF-8 CSV with stable references and timezone-aware timestamps. | Contract and fixtures implemented | Real partner onboarding |
| Shared immutable object semantics can preserve partner evidence. | Local and MinIO adapters tested | Production retention/security review |
| Operations needs minute-level data and Finance a daily cycle. | Design assumption | SLA benchmark and stakeholder approval |
| Generated volumes represent production scale. | Not claimed | Workload benchmark in a later phase |
