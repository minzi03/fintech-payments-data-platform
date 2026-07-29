# Business Case

## Business context

The project models a hypothetical mid-sized fintech providing payment gateway integration, merchant
payments, account-to-account transfers, refunds, and partner-bank settlement. Internal users include
Payment Operations, Finance, Risk, Product, Customer Support, Compliance, Analytics, and Data
Engineering.

The implemented data platform supplies a constrained OLTP source, repeatable synthetic payment
data, versioned partner-settlement intake, PostgreSQL CDC through Debezium/Kafka, immutable MinIO
Bronze, typed PyArrow Silver outputs, and Airflow orchestration/control evidence. A separate Portal
runtime demonstrates identity, server-side sessions, abuse protection, audit, and operational
security boundaries. It does not operate the data plane.

Reconciliation products, warehouse/dbt transformations, business dashboards, production
deployment, and HA/DR remain unimplemented.

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

Operations could monitor volume, success/failure rates, latency, refunds, channel health, and
pending backlog. The local CDC path now carries row changes through logical WAL, Debezium, Kafka,
immutable Bronze, and Silver. It demonstrates near-real-time transport locally but has no
production latency SLO and does not provide an operations dashboard.

### Daily settlement reconciliation

Finance could compare completed internal transactions with partner settlement lines and classify
matches, missing items, duplicates, amount/currency mismatches, and status mismatches. The current
platform validates and preserves partner evidence through Bronze/Silver quality boundaries.
Matching and reconciliation-product logic remain deliberately unimplemented.

## Stakeholders

| Stakeholder        | Need                          | Current implemented contribution                              |
| ------------------ | ----------------------------- | ------------------------------------------------------------- |
| Payment Operations | Status and failure visibility | Reproducible lifecycle source data                            |
| Finance            | Auditable settlement matching | Fixed-precision partner evidence and replay-safe manifest     |
| Product/Risk       | Consistent domain behavior    | Validated transaction types, channels, and statuses           |
| Customer Support   | Payment/refund lookup         | Related current-state records without sensitive identity data |
| Data Engineering   | Stable source contracts       | Versioned SQL/CSV contracts, generators, tests, and runbooks  |
| Compliance         | Minimal data exposure         | No national ID, card data, or real credentials                |

## Expected value

The implemented source-to-Silver path reduces downstream ambiguity through source grain, lifecycle
rules, precision, CDC coordinates, content identity, quarantine, deterministic fixtures, typed
outputs, lineage, and bounded replay controls. Portal security evidence improves the operational
engineering story without claiming a unified control plane. Business analytics outcomes remain
targets until executable data products and measured use exist.

## Assumptions and validation

| Assumption                                                                           | Current status                                        | Later validation                           |
| ------------------------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------------ |
| Payments expose current state plus immutable lifecycle events.                       | Modeled and carried through the local CDC/Silver path | Production contract and latency validation |
| A customer may own multiple single-currency accounts.                                | Modeled; overdraft excluded                           | Product policy review                      |
| Merchant payments and account transfers cover the first source slice.                | Implemented generator scope                           | Contract review before CDC                 |
| Partner references are unique when present.                                          | Database constraint                                   | Settlement partner contract review         |
| Partners can provide UTF-8 CSV with stable references and timezone-aware timestamps. | Contract and fixtures implemented                     | Real partner onboarding                    |
| Shared immutable object semantics can preserve partner evidence.                     | Local and MinIO adapters tested                       | Production retention/security review       |
| Operations needs minute-level data and Finance a daily cycle.                        | Design assumption; local CDC has no production SLO    | SLA benchmark and stakeholder approval     |
| Generated volumes represent production scale.                                        | Not claimed                                           | Workload benchmark in a later phase        |
