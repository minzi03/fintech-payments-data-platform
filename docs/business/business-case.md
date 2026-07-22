# Business Case

## Business context

The project models a hypothetical mid-sized fintech providing payment gateway integration, merchant
payments, account-to-account transfers, refunds, and partner-bank settlement. Internal users include
Payment Operations, Finance, Risk, Product, Customer Support, Compliance, Analytics, and Data
Engineering.

Phase 1 supplies a constrained OLTP source and repeatable synthetic data for development. It does not
deliver operational analytics or settlement reconciliation yet.

## Payment products represented in Phase 1

- Merchant payments through card, bank transfer, QR, or wallet channels.
- Account-to-account transfers between customer accounts.
- Successful, failed, and pending payment lifecycles.
- Full and partial refunds linked to completed transactions.
- External partner references needed by future settlement matching.

Partner settlement files and records are deliberately deferred to the batch-ingestion phase.

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
matches, missing items, duplicates, amount/currency mismatches, and status mismatches. Phase 1 stores
unique partner references on transactions/refunds, but partner files and matching logic are not yet
implemented.

## Stakeholders

| Stakeholder | Need | Phase 1 contribution |
| --- | --- | --- |
| Payment Operations | Status and failure visibility | Reproducible lifecycle source data |
| Finance | Auditable settlement matching | Unique partner references and fixed-precision money |
| Product/Risk | Consistent domain behavior | Validated transaction types, channels, and statuses |
| Customer Support | Payment/refund lookup | Related current-state records without sensitive identity data |
| Data Engineering | Stable source contracts | Versioned SQL, generator, tests, and runbook |
| Compliance | Minimal data exposure | No national ID, card data, or real credentials |

## Expected value

Phase 1 reduces downstream ambiguity by establishing source grain, relationships, lifecycle rules,
precision, and deterministic test fixtures. Business outcomes such as shorter incident detection or
on-time reconciliation remain target outcomes until later data products are implemented and measured.

## Assumptions and validation

| Assumption | Phase 1 status | Later validation |
| --- | --- | --- |
| Payments expose current state plus immutable lifecycle events. | Modeled and tested locally | CDC/event pipeline replay tests |
| A customer may own multiple single-currency accounts. | Modeled; overdraft excluded | Product policy review |
| Merchant payments and account transfers cover the first source slice. | Implemented generator scope | Contract review before CDC |
| Partner references are unique when present. | Database constraint | Settlement partner contract review |
| Operations needs minute-level data and Finance a daily cycle. | Design assumption | SLA benchmark and stakeholder approval |
| Generated volumes represent production scale. | Not claimed | Workload benchmark in a later phase |
