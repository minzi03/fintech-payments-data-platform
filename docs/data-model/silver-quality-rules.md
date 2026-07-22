# Silver Quality Rules

## Error taxonomy

| Code | Policy |
| --- | --- |
| `INVALID_BRONZE_SCHEMA` | Quarantine an unreadable/incompatible object or reject the affected row |
| `SCHEMA_VERSION_UNSUPPORTED` | Quarantine until an explicit compatibility implementation exists |
| `INVALID_JSON` | Reject row; Bronze remains the forensic source |
| `MISSING_BUSINESS_KEY` | Reject from history and state |
| `INVALID_DECIMAL` | Keep structurally valid history, reject state projection |
| `INVALID_TIMESTAMP` | Keep structurally valid history, reject state projection |
| `UNSUPPORTED_OPERATION` | Reject row |
| `DUPLICATE_COORDINATE` | Keep first deterministic coordinate, reject duplicate |
| `DUPLICATE_EVENT` | Keep first deterministic event ID, reject duplicate |
| `OUT_OF_ORDER_EVENT` | Keep audit history, do not regress latest state |
| `INVALID_REFERENCE` | Reject a missing hard-required reference |

Rejections contain source object URI, event ID where available, entity/business key, code/message,
a payload-free row/coordinate reference, run ID, and UTC rejection time. They never include full
before/after/customer records in logs or the rejection reference. Any prior manifest identity,
including a failed attempt, requires explicit `--force-reprocess` before another attempt starts.

## Delete and replay policy

Delete events remain in history and make `latest_all.is_deleted=true`. Current filters them only
after latest state is selected. Tombstones retain audit metadata and reuse the last non-null state
payload. The same input identity is skipped; force processing creates new lineage from the latest
completed snapshot whose input checksum differs from the replayed object. A replay with the same
run/key/checksum is idempotent; conflicting bytes never overwrite.

## Reference policy

`accounts.customer_id`, transaction customer/account/optional merchant, and refund transaction
relationships are evaluated against the latest completed current snapshots. Empty required fields
are invalid. A well-formed but not-yet-observed key is `TEMPORARILY_UNRESOLVED`, written separately,
and does not reject the business row. Phase 6 does not claim strict cross-stream referential timing.

## Settlement policy

The original versioned contract remains authoritative: exact header, Decimal precision/scale,
timezone, partner/date filename agreement, status/currency formats, unique reference, duplicate row,
and amount-minus-fee net consistency. Valid rows continue when row-level failures exist; no internal
transaction reconciliation occurs.
