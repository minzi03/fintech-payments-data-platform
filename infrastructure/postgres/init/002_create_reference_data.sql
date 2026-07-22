BEGIN;

SET search_path TO payments, public;

INSERT INTO currencies (currency_code, currency_name, minor_units)
VALUES
    ('USD', 'United States Dollar', 2),
    ('EUR', 'Euro', 2),
    ('GBP', 'Pound Sterling', 2),
    ('SGD', 'Singapore Dollar', 2),
    ('VND', 'Vietnamese Dong', 0)
ON CONFLICT (currency_code) DO UPDATE
SET
    currency_name = EXCLUDED.currency_name,
    minor_units = EXCLUDED.minor_units,
    is_active = TRUE;

INSERT INTO payment_channels (payment_channel_code, display_name)
VALUES
    ('CARD', 'Card'),
    ('BANK_TRANSFER', 'Bank Transfer'),
    ('QR', 'QR Payment'),
    ('WALLET', 'Digital Wallet')
ON CONFLICT (payment_channel_code) DO UPDATE
SET
    display_name = EXCLUDED.display_name,
    is_active = TRUE;

INSERT INTO merchant_categories (category_code, category_name)
VALUES
    ('4814', 'Telecommunication Services'),
    ('5411', 'Grocery Stores and Supermarkets'),
    ('5732', 'Electronics Stores'),
    ('5812', 'Eating Places and Restaurants'),
    ('6012', 'Financial Institutions')
ON CONFLICT (category_code) DO UPDATE
SET
    category_name = EXCLUDED.category_name,
    is_active = TRUE;

COMMIT;
