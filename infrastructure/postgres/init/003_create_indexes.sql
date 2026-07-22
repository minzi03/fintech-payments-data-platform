BEGIN;

SET search_path TO payments, public;

CREATE UNIQUE INDEX IF NOT EXISTS ux_customers_email_case_insensitive
    ON customers (LOWER(email))
    WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_customers_status
    ON customers (status);

CREATE INDEX IF NOT EXISTS ix_accounts_customer_status
    ON accounts (customer_id, status);

CREATE INDEX IF NOT EXISTS ix_merchants_status_category
    ON merchants (status, category_code);

CREATE INDEX IF NOT EXISTS ix_merchants_country
    ON merchants (country_code);

CREATE INDEX IF NOT EXISTS ix_payment_transactions_status_requested
    ON payment_transactions (status, requested_at DESC);

CREATE INDEX IF NOT EXISTS ix_payment_transactions_customer_requested
    ON payment_transactions (customer_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS ix_payment_transactions_account_requested
    ON payment_transactions (account_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS ix_payment_transactions_merchant_requested
    ON payment_transactions (merchant_id, requested_at DESC)
    WHERE merchant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_payment_transactions_destination_account
    ON payment_transactions (destination_account_id, requested_at DESC)
    WHERE destination_account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_transaction_events_transaction_time
    ON transaction_events (transaction_id, event_time, event_id);

CREATE INDEX IF NOT EXISTS ix_transaction_events_type_time
    ON transaction_events (event_type, event_time DESC);

CREATE INDEX IF NOT EXISTS ix_transaction_events_trace_id
    ON transaction_events (trace_id);

CREATE INDEX IF NOT EXISTS ix_refunds_transaction_status
    ON refunds (transaction_id, status, requested_at DESC);

COMMIT;
