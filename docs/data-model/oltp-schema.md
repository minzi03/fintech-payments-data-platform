# PostgreSQL OLTP Schema

## Scope and initialization

Phase 1 implements schema `payments` through three ordered scripts:

1. `001_create_database_objects.sql` creates reference/domain tables and triggers.
2. `002_create_reference_data.sql` upserts controlled currencies, channels, and categories.
3. `003_create_indexes.sql` creates query and lookup indexes.

The official PostgreSQL image runs these files only when the named data volume is initialized from an
empty state. Statements use `IF NOT EXISTS`, conflict-safe seed operations, and replaceable functions
where practical, but these scripts are an initialization baseline rather than a general migration
engine. A schema change after initialization requires a versioned migration in a future phase or the
documented destructive local reset.

## Table definitions

### Reference tables

| Table | Purpose and grain | Key/rules |
| --- | --- | --- |
| `currencies` | One supported ISO-like three-letter currency | PK `currency_code`; uppercase format check; active flag |
| `payment_channels` | One normalized transaction route | PK `payment_channel_code`; active flag |
| `merchant_categories` | One normalized merchant category | PK `category_code`; unique name; active flag |

### `customers`

- **Purpose/grain:** one current row per customer.
- **Primary key:** `customer_id` UUID.
- **Unique:** normalized non-null email through a partial lower-case index.
- **Checks:** status is `PENDING_VERIFICATION`, `ACTIVE`, `SUSPENDED`, or `CLOSED`; email cannot be blank; timestamps are
  ordered.
- **Timestamps:** `created_at`, `updated_at`; updates refresh `updated_at`.
- **Indexes:** status and case-insensitive email.
- **Data minimization:** name and optional synthetic email only; no national ID or payment credential.

### `accounts`

- **Purpose/grain:** one current single-currency account owned by one customer.
- **Primary/foreign keys:** `account_id`; `customer_id -> customers`; `currency -> currencies`.
- **Unique:** `account_number`, an opaque business-facing synthetic identifier.
- **Checks:** `balance NUMERIC(18,2) >= 0`; status is `PENDING`, `ACTIVE`, `FROZEN`, or `CLOSED`; currency is an
  uppercase three-letter code; timestamps are ordered.
- **Timestamps/indexes:** `created_at`, `updated_at`; indexes by customer and status.
- **Scope rule:** overdraft is not supported in Phase 1.

### `merchants`

- **Purpose/grain:** one current merchant.
- **Primary/foreign keys:** `merchant_id`; `category_code -> merchant_categories`.
- **Unique:** `merchant_code` business key and non-null `external_reference` used by future settlement.
- **Checks:** status is `ACTIVE`, `SUSPENDED`, `INACTIVE`, or `CLOSED`; country is uppercase ISO-like
  two-letter text; keys/name are nonblank; timestamps are ordered.
- **Timestamps/indexes:** `created_at`, `updated_at`; indexes by status, category, and country.

### `payment_transactions`

- **Purpose/grain:** one current row per initiated payment.
- **Primary key:** `transaction_id` UUID.
- **Foreign keys:** customer, source account, optional destination account, optional merchant,
  channel, and currency.
- **Unique:** mandatory `idempotency_key`; partial unique `partner_reference` when present.
- **Money/checks:** `amount NUMERIC(18,2) > 0`; uppercase three-letter currency.
- **Type semantics:** `MERCHANT_PAYMENT` requires a merchant and forbids a destination account;
  `ACCOUNT_TRANSFER` requires a different destination account and forbids a merchant.
- **Status:** `PENDING`, `AUTHORIZED`, `COMPLETED`, or `FAILED`.
- **Timestamp semantics:** `requested_at` is mandatory. `COMPLETED` requires only `completed_at`;
  `FAILED` requires only `failed_at`; in-flight statuses have neither. Terminal times cannot precede
  the request. `created_at` and `updated_at` are ordered audit timestamps.
- **Indexes:** customer/request time, source account/request time, merchant/request time, destination
  account/request time, status/request time, and partner-reference lookup.

### `transaction_events`

- **Purpose/grain:** one immutable business transition for one transaction.
- **Primary/foreign keys:** `event_id`; `transaction_id -> payment_transactions`.
- **Unique:** `(transaction_id, event_version)` provides an ordered per-transaction event sequence.
- **Event fields:** optional previous status, required new status, `event_time`, `producer_time`,
  `trace_id`, `source_system`, and JSONB object payload.
- **Checks:** event type and previous/new statuses are allowed; version is positive; producer time
  cannot precede event time; transition columns agree with the event type.
- **Immutability:** a trigger rejects `UPDATE` and `DELETE` with SQLSTATE `55000`. Operational repair
  requires an explicit privileged process outside normal application flow.
- **Indexes:** `(transaction_id, event_time)`, `event_time`, event type/time, and trace ID.

### `refunds`

- **Purpose/grain:** one current refund request or lifecycle linked to an original transaction.
- **Primary/foreign keys:** `refund_id`; `transaction_id -> payment_transactions`; currency reference.
- **Unique:** partial unique `partner_reference` when present.
- **Checks:** `amount NUMERIC(18,2) > 0`; status is `PENDING`, `COMPLETED`, `FAILED`, or `CANCELLED`;
  terminal timestamps agree with status; reason is nonblank; timestamps are ordered.
- **Indexes:** transaction/request time and status/request time.
- **Cross-row rule:** a row-local `CHECK` cannot compare the original transaction or sum sibling
  refunds. `PaymentRepository` locks the original transaction, requires original status `COMPLETED`,
  requires equal currency, and rejects a cumulative refund greater than the original amount. The
  integration suite verifies both currency and cumulative-amount rejection. All application writers
  must use an equivalent transactional rule.

## Payment lifecycle

```text
REQUESTED event -> PENDING
PENDING -> AUTHORIZED -> COMPLETED
PENDING ----------------> COMPLETED
PENDING ----------------> FAILED
AUTHORIZED -------------> FAILED
```

The Phase 1 generator produces direct pending-to-completed, pending-to-failed, and optionally
authorized-to-completed/failed paths. Terminal transaction rows do not transition again.

## Refund lifecycle

```text
PENDING -> COMPLETED
PENDING -> FAILED
PENDING -> CANCELLED
```

The current generator emits valid completed or failed refund rows and corresponding payment-linked
data; a later event contract may introduce dedicated refund events.

## Known Phase 1 limitations

- No cross-currency payment, overdraft, fee, chargeback, dispute, settlement record, or ledger.
- Account balance movements are not posted; balances exist to model account state, not double-entry
  accounting.
- Database status checks constrain values but do not implement a state-machine trigger for updates;
  application services must follow the documented lifecycle.
- PostgreSQL is a local single-node source with no HA, TLS, backup policy, or migration framework.
