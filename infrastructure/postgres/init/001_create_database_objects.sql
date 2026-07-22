BEGIN;

CREATE SCHEMA IF NOT EXISTS payments;
SET search_path TO payments, public;

CREATE TABLE IF NOT EXISTS currencies (
    currency_code CHAR(3) PRIMARY KEY,
    currency_name VARCHAR(64) NOT NULL,
    minor_units SMALLINT NOT NULL DEFAULT 2,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT currencies_code_format CHECK (currency_code ~ '^[A-Z]{3}$'),
    CONSTRAINT currencies_minor_units_range CHECK (minor_units BETWEEN 0 AND 4),
    CONSTRAINT currencies_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS payment_channels (
    payment_channel_code VARCHAR(32) PRIMARY KEY,
    display_name VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT payment_channels_code_format
        CHECK (payment_channel_code ~ '^[A-Z][A-Z0-9_]*$'),
    CONSTRAINT payment_channels_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS merchant_categories (
    category_code VARCHAR(4) PRIMARY KEY,
    category_name VARCHAR(128) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT merchant_categories_code_format CHECK (category_code ~ '^[0-9]{4}$'),
    CONSTRAINT merchant_categories_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id UUID PRIMARY KEY,
    external_customer_ref VARCHAR(64) NOT NULL UNIQUE,
    full_name VARCHAR(200) NOT NULL,
    email VARCHAR(320),
    country_code CHAR(2) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT customers_country_code_format CHECK (country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT customers_email_shape CHECK (email IS NULL OR POSITION('@' IN email) > 1),
    CONSTRAINT customers_status_valid
        CHECK (status IN ('PENDING_VERIFICATION', 'ACTIVE', 'SUSPENDED', 'CLOSED')),
    CONSTRAINT customers_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    account_number VARCHAR(34) NOT NULL UNIQUE,
    currency CHAR(3) NOT NULL REFERENCES currencies(currency_code) ON DELETE RESTRICT,
    balance NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    status VARCHAR(24) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT accounts_balance_non_negative CHECK (balance >= 0.00),
    CONSTRAINT accounts_status_valid
        CHECK (status IN ('PENDING', 'ACTIVE', 'FROZEN', 'CLOSED')),
    CONSTRAINT accounts_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS merchants (
    merchant_id UUID PRIMARY KEY,
    merchant_code VARCHAR(64) NOT NULL UNIQUE,
    external_reference VARCHAR(64) NOT NULL UNIQUE,
    merchant_name VARCHAR(200) NOT NULL,
    category_code VARCHAR(4) NOT NULL
        REFERENCES merchant_categories(category_code) ON DELETE RESTRICT,
    country_code CHAR(2) NOT NULL,
    settlement_currency CHAR(3) NOT NULL
        REFERENCES currencies(currency_code) ON DELETE RESTRICT,
    status VARCHAR(24) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT merchants_country_code_format CHECK (country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT merchants_code_format CHECK (merchant_code ~ '^[A-Z][A-Z0-9_-]*$'),
    CONSTRAINT merchants_status_valid CHECK (status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE', 'CLOSED')),
    CONSTRAINT merchants_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS payment_transactions (
    transaction_id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
    destination_account_id UUID REFERENCES accounts(account_id) ON DELETE RESTRICT,
    merchant_id UUID REFERENCES merchants(merchant_id) ON DELETE RESTRICT,
    transaction_type VARCHAR(32) NOT NULL,
    payment_channel VARCHAR(32) NOT NULL
        REFERENCES payment_channels(payment_channel_code) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL,
    currency CHAR(3) NOT NULL REFERENCES currencies(currency_code) ON DELETE RESTRICT,
    status VARCHAR(24) NOT NULL,
    partner_reference VARCHAR(128) UNIQUE,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    failure_code VARCHAR(64),
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT payment_transactions_amount_positive CHECK (amount > 0.00),
    CONSTRAINT payment_transactions_type_valid
        CHECK (transaction_type IN ('MERCHANT_PAYMENT', 'ACCOUNT_TRANSFER')),
    CONSTRAINT payment_transactions_status_valid
        CHECK (status IN ('PENDING', 'AUTHORIZED', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT payment_transactions_target_valid CHECK (
        (
            transaction_type = 'MERCHANT_PAYMENT'
            AND merchant_id IS NOT NULL
            AND destination_account_id IS NULL
        )
        OR
        (
            transaction_type = 'ACCOUNT_TRANSFER'
            AND merchant_id IS NULL
            AND destination_account_id IS NOT NULL
            AND destination_account_id <> account_id
        )
    ),
    CONSTRAINT payment_transactions_status_timestamps_valid CHECK (
        (
            status = 'COMPLETED'
            AND completed_at IS NOT NULL
            AND failed_at IS NULL
        )
        OR
        (
            status = 'FAILED'
            AND failed_at IS NOT NULL
            AND completed_at IS NULL
        )
        OR
        (
            status IN ('PENDING', 'AUTHORIZED', 'CANCELLED')
            AND completed_at IS NULL
            AND failed_at IS NULL
        )
    ),
    CONSTRAINT payment_transactions_completed_after_request
        CHECK (completed_at IS NULL OR completed_at >= requested_at),
    CONSTRAINT payment_transactions_failed_after_request
        CHECK (failed_at IS NULL OR failed_at >= requested_at),
    CONSTRAINT payment_transactions_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS transaction_events (
    event_id UUID PRIMARY KEY,
    transaction_id UUID NOT NULL
        REFERENCES payment_transactions(transaction_id) ON DELETE RESTRICT,
    event_type VARCHAR(40) NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1,
    previous_status VARCHAR(24),
    new_status VARCHAR(24) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    producer_time TIMESTAMPTZ NOT NULL,
    trace_id UUID NOT NULL,
    source_system VARCHAR(64) NOT NULL,
    event_payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT transaction_events_transaction_version_unique
        UNIQUE (transaction_id, event_version),
    CONSTRAINT transaction_events_version_positive CHECK (event_version > 0),
    CONSTRAINT transaction_events_type_valid CHECK (
        event_type IN (
            'PAYMENT_REQUESTED',
            'PAYMENT_AUTHORIZED',
            'PAYMENT_COMPLETED',
            'PAYMENT_FAILED'
        )
    ),
    CONSTRAINT transaction_events_previous_status_valid CHECK (
        previous_status IS NULL
        OR previous_status IN ('PENDING', 'AUTHORIZED', 'COMPLETED', 'FAILED', 'CANCELLED')
    ),
    CONSTRAINT transaction_events_new_status_valid
        CHECK (new_status IN ('PENDING', 'AUTHORIZED', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT transaction_events_transition_valid CHECK (
        (
            event_type = 'PAYMENT_REQUESTED'
            AND previous_status IS NULL
            AND new_status = 'PENDING'
        )
        OR
        (
            event_type = 'PAYMENT_AUTHORIZED'
            AND previous_status = 'PENDING'
            AND new_status = 'AUTHORIZED'
        )
        OR
        (
            event_type = 'PAYMENT_COMPLETED'
            AND previous_status IN ('PENDING', 'AUTHORIZED')
            AND new_status = 'COMPLETED'
        )
        OR
        (
            event_type = 'PAYMENT_FAILED'
            AND previous_status IN ('PENDING', 'AUTHORIZED')
            AND new_status = 'FAILED'
        )
    ),
    CONSTRAINT transaction_events_payload_object
        CHECK (JSONB_TYPEOF(event_payload) = 'object'),
    CONSTRAINT transaction_events_producer_after_event
        CHECK (producer_time >= event_time),
    CONSTRAINT transaction_events_created_after_event
        CHECK (created_at >= event_time)
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_id UUID PRIMARY KEY,
    transaction_id UUID NOT NULL
        REFERENCES payment_transactions(transaction_id) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL,
    currency CHAR(3) NOT NULL REFERENCES currencies(currency_code) ON DELETE RESTRICT,
    status VARCHAR(24) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    partner_reference VARCHAR(128) UNIQUE,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT refunds_amount_positive CHECK (amount > 0.00),
    CONSTRAINT refunds_status_valid CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT refunds_reason_code_format CHECK (reason_code ~ '^[A-Z][A-Z0-9_]*$'),
    CONSTRAINT refunds_status_timestamps_valid CHECK (
        (status = 'COMPLETED' AND completed_at IS NOT NULL)
        OR (status IN ('PENDING', 'FAILED', 'CANCELLED') AND completed_at IS NULL)
    ),
    CONSTRAINT refunds_completed_after_request
        CHECK (completed_at IS NULL OR completed_at >= requested_at),
    CONSTRAINT refunds_timestamps_ordered CHECK (updated_at >= created_at)
);

CREATE OR REPLACE FUNCTION payments.set_updated_at()
RETURNS TRIGGER
LANGUAGE PLPGSQL
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS currencies_set_updated_at ON currencies;
CREATE TRIGGER currencies_set_updated_at
BEFORE UPDATE ON currencies
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS payment_channels_set_updated_at ON payment_channels;
CREATE TRIGGER payment_channels_set_updated_at
BEFORE UPDATE ON payment_channels
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS merchant_categories_set_updated_at ON merchant_categories;
CREATE TRIGGER merchant_categories_set_updated_at
BEFORE UPDATE ON merchant_categories
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS customers_set_updated_at ON customers;
CREATE TRIGGER customers_set_updated_at
BEFORE UPDATE ON customers
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS accounts_set_updated_at ON accounts;
CREATE TRIGGER accounts_set_updated_at
BEFORE UPDATE ON accounts
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS merchants_set_updated_at ON merchants;
CREATE TRIGGER merchants_set_updated_at
BEFORE UPDATE ON merchants
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS payment_transactions_set_updated_at ON payment_transactions;
CREATE TRIGGER payment_transactions_set_updated_at
BEFORE UPDATE ON payment_transactions
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

DROP TRIGGER IF EXISTS refunds_set_updated_at ON refunds;
CREATE TRIGGER refunds_set_updated_at
BEFORE UPDATE ON refunds
FOR EACH ROW EXECUTE FUNCTION payments.set_updated_at();

CREATE OR REPLACE FUNCTION payments.reject_transaction_event_mutation()
RETURNS TRIGGER
LANGUAGE PLPGSQL
AS $$
BEGIN
    RAISE EXCEPTION 'transaction_events are immutable'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS transaction_events_immutable ON transaction_events;
CREATE TRIGGER transaction_events_immutable
BEFORE UPDATE OR DELETE ON transaction_events
FOR EACH ROW EXECUTE FUNCTION payments.reject_transaction_event_mutation();

COMMIT;
