# KPI Catalog Skeleton

Every KPI requires an owner, semantic definition, grain, time zone, inclusion/exclusion rules, source
mart, freshness expectation, and measurement status.

## Payment operations

| KPI | Definition skeleton | Planned grain | Status |
| --- | --- | --- | --- |
| Transactions per minute | Count of accepted payment requests by event minute. | Minute, channel | Not implemented |
| Success rate | Completed eligible payments divided by terminal eligible payments. | Time window, channel | Not implemented |
| Failure rate | Failed eligible payments divided by terminal eligible payments. | Time window, channel, error code | Not implemented |
| Processing latency | Completion event time minus request event time. | Transaction | Not implemented |
| Refund rate | Refunded completed payments divided by completed payments. | Business date, merchant | Not implemented |

## Settlement reconciliation

| KPI | Definition skeleton | Planned grain | Status |
| --- | --- | --- | --- |
| Reconciliation success rate | Matched eligible items divided by all eligible reconciliation items. | Partner, settlement date | Not implemented |
| Outstanding amount | Sum of unresolved internal or settlement amounts under an agreed sign convention. | Partner, currency, reason | Not implemented |
| Mismatch count | Count of reconciliation results not classified as `MATCHED`. | Partner, settlement date, reason | Not implemented |

KPI semantics will be approved with Finance and Operations before dashboard implementation.
