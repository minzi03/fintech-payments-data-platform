# Banking Partner Settlement Contract

## Implementation status

`contracts/batch/settlement_v1.yml` is implemented and executable in Phase 2. It governs partner CSV
validation before immutable local Bronze storage. Reconciliation with internal transactions is
planned for a later phase and is not performed by this contract.

## Ownership and classification

| Attribute | Value |
| --- | --- |
| Source owner | Banking Partnerships |
| Data product owner | Finance Data Products |
| Schema version | `settlement-v1` |
| Contract version | `1.0.0` |
| Classification | Confidential financial |
| Format/encoding | CSV, UTF-8, comma delimiter, header required |

The contract contains transaction references and financial amounts but no card PAN, bank credential,
customer name, national ID, or authentication secret.

## File naming convention

```text
settlement_<partner_id>_<settlement_date>_<sequence>.csv
settlement_VCB_2026-07-22_001.csv
```

- `partner_id`: 2-16 uppercase alphanumeric characters, starting with a letter.
- `settlement_date`: a valid ISO `YYYY-MM-DD` date.
- `sequence`: `001` through `999`.
- Extension is exactly lowercase `.csv`.
- Partner encoded in the name must equal the CLI partner and every record's `partner_id`.

## Grain and business key

The file grain is one partner settlement delivery for one partner, date, and sequence. Row grain is
one settlement item reported by the partner. The declared business key is:

```text
(partner_id, settlement_date, settlement_reference)
```

`settlement_reference` must also be unique within each file. Duplicate raw rows are rejected at row
level but remain present in the immutable Bronze copy.

## Fields

| Field | Required | Nullable | Type and rule |
| --- | --- | --- | --- |
| `partner_id` | Yes | No | Uppercase partner code; matches file name |
| `settlement_date` | Yes | No | ISO date; matches file name |
| `settlement_reference` | Yes | No | String, maximum 128 characters; in-file unique |
| `partner_transaction_reference` | Yes | No | String, maximum 128 characters |
| `internal_transaction_id` | Yes | Yes | UUID when present; blank represents a missing-internal candidate |
| `transaction_timestamp` | Yes | No | ISO-8601 with explicit offset; normalized to UTC in memory |
| `amount` | Yes | No | Decimal `(18,2)`, greater than zero |
| `currency` | Yes | No | Three uppercase letters |
| `settlement_status` | Yes | No | `SETTLED`, `REVERSED`, or `FAILED` |
| `fee_amount` | Yes | No | Decimal `(18,2)`, zero or greater |
| `net_amount` | Yes | No | Decimal `(18,2)`; equals `amount - fee_amount` |

Python uses `Decimal`; no financial value is parsed as `float`.

## Supported scenario fixtures

The deterministic fixture generator creates evidence for:

- `MATCHED` candidate;
- `MISSING_INTERNAL` candidate;
- `AMOUNT_MISMATCH` candidate;
- `CURRENCY_MISMATCH` candidate;
- `STATUS_MISMATCH` candidate;
- `DUPLICATE_SETTLEMENT` candidate;
- invalid amount, currency, status, and duplicate records;
- invalid file schema;
- header-only empty file;
- the same file name with changed content.

These labels only make later reconciliation cases reproducible. Phase 2 does not look up
`payments.payment_transactions` or assign reconciliation outcomes.

## Validation and retention semantics

- File-level failures include invalid naming, encoding, CSV header/schema, or empty files. The raw
  file is copied to quarantine and is not written to Bronze.
- Row-level failures allow partial acceptance by default. The source file is copied byte-for-byte to
  Bronze and rejected rows are written as JSON Lines to quarantine.
- `--fail-on-rejected-records` changes row failures to strict file quarantine.
- Inbound files are never deleted automatically.
- SHA-256 identifies content; successfully processed content is not processed again.
