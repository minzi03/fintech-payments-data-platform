# Dimensional Model Skeleton

## Planned dimensions

| Dimension | Business key | History strategy |
| --- | --- | --- |
| `dim_customer` | `customer_id` | SCD Type 2 for material profile/segment changes; Type 1 for corrections |
| `dim_account` | `account_id` | SCD Type 2 for status, product, and ownership changes |
| `dim_merchant` | `merchant_id` | SCD Type 2 for category, status, and settlement configuration |
| `dim_payment_channel` | `payment_channel_code` | Type 1 unless business history requires versioning |
| `dim_currency` | `currency_code` | Type 1 reference data |
| `dim_date` | calendar date | Static role-playing dimension |
| `dim_time` | second or minute key | Static role-playing dimension |

SCD Type 2 dimensions will use a surrogate key, `valid_from`, `valid_to`, and `is_current`. A business
key must have one current row and non-overlapping validity intervals. Event-time facts must resolve the
dimension version valid when the business event occurred.

## Planned facts

| Fact | Grain | Primary use |
| --- | --- | --- |
| `fct_payment_transactions` | One row per `transaction_id` at its latest accepted state | Operations and executive KPIs |
| `fct_transaction_events` | One row per immutable `event_id` | Lifecycle latency and state transitions |
| `fct_refunds` | One row per `refund_id` | Refund operations and merchant behavior |
| `fct_settlement_reconciliation` | One row per reconciliation matching result | Finance and Operations investigation |
| `fct_merchant_daily_performance` | One row per merchant, business date, currency, and channel | Merchant performance |
| `fct_pipeline_sla` | One row per pipeline run and monitored dataset | Platform observability |

## Incremental design constraints

- Facts use deterministic unique keys and merge semantics.
- Incremental filters include a configurable late-arriving lookback.
- Updates and deletes are explicitly represented instead of silently discarded.
- Reprocessing the same source batch or offset range produces the same accepted result.
- Reconciliation preserves the original evidence and matching-rule version.

Detailed SQL, physical types, clustering, and dbt contracts are deferred until the source model and
warehouse environments exist.
