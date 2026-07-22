# Business Case

## Business context

The project models a hypothetical mid-sized fintech that provides payment gateway integration,
merchant payments, customer transfers, refunds, and settlement with banking partners.

Customers include online merchants, physical merchants, and consumers who initiate or receive
payments. Internal users include Payment Operations, Finance, Risk, Product, Customer Support,
Executive, Analytics, and Data Engineering teams.

All scale figures remain **design assumptions** until Phase 1 defines a deterministic generator and a
later benchmark records observed results.

## Payment products

- Payment gateway APIs for online merchants.
- QR and merchant payments for online and physical acceptance channels.
- Customer-to-customer and customer-to-merchant transfers.
- Full and partial refunds linked to the original payment.
- Daily settlement and reconciliation with banking partners.

## Current business problems

- Payment status and failure data are fragmented across operational systems and events.
- Operations cannot consistently identify a failing channel or error-code spike within minutes.
- Finance receives daily settlement files that must be compared with internal transactions.
- Reprocessing can create duplicates without manifests, deterministic keys, and durable audit state.
- Different consumers can calculate payment and reconciliation metrics differently.
- Pipeline freshness, completeness, ownership, and lineage are not visible in one place.

## Primary use case 1: payment operations monitoring

Payment Operations needs a near-real-time view of volume, success rate, failure rate, processing
latency, refunds, channel health, and backlog. The platform must preserve event history while also
providing a trustworthy latest state for each payment.

Decisions supported:

- Investigate a channel, merchant, or error code when failure rates cross an agreed threshold.
- Escalate payment-processing latency or event backlog before customer impact grows.
- Compare current behavior with a recent baseline.

## Primary use case 2: daily settlement reconciliation

Finance and Operations need to match internal completed transactions with settlement lines received
from banking partners. The result must classify matches and mismatches without overwriting evidence.

Initial result classes:

- `MATCHED`
- `MISSING_INTERNAL`
- `MISSING_SETTLEMENT`
- `AMOUNT_MISMATCH`
- `CURRENCY_MISMATCH`
- `STATUS_MISMATCH`
- `DUPLICATE_INTERNAL`
- `DUPLICATE_SETTLEMENT`

Decisions supported:

- Release or hold finance reports based on completeness and mismatch thresholds.
- Assign unmatched items for investigation using partner, currency, date, and reason.
- Re-run a specific file or batch without duplicating prior results.

## Stakeholders

| Stakeholder | Need | Planned output |
| --- | --- | --- |
| Payment Operations | Fast channel and failure visibility | Operations mart, dashboard, alerts |
| Finance | Auditable settlement matching | Reconciliation fact and finance dashboard |
| Product | Merchant and payment-channel trends | Product and executive marts |
| Risk | Reliable payment and device events | Governed risk-ready Silver data |
| Customer Support | Payment and refund lookup | Restricted support-facing data product |
| Data Engineering | Recoverable, observable pipelines | Control tables, metrics, lineage, runbooks |
| Compliance | PII controls and audit history | Classification, masking, access evidence |

## Expected value

Expected value will be evaluated through measurable outcomes such as shorter detection delay,
reconciliation completion before the reporting deadline, reproducible reprocessing, and consistent KPI
definitions. No outcome is claimed as achieved during Phase 0.

## Assumptions and validation status

| Assumption | Current status | Planned validation |
| --- | --- | --- |
| The fintech operates at a scale that benefits from both batch and event processing. | Design assumption | Phase 1 workload profile and later benchmarks |
| Banking partners deliver one or more settlement files daily. | Design assumption | Phase 1 batch contracts and fixtures |
| Payment lifecycle state is available through OLTP records and domain events. | Design assumption | Phase 1 source model and event contracts |
| Operations needs minute-level visibility while Finance works on a daily cycle. | Design assumption | Stakeholder review and KPI approval |
| The platform can use Snowflake for governed analytics. | Target-state assumption | Environment and cost review before Phase 6 |
