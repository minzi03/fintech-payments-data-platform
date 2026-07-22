# Bronze-to-Silver Processing Architecture

## Status and boundary

Implemented in Phase 6 as a Python 3.11+/PyArrow CLI. It reads immutable Bronze objects through the
shared local/MinIO storage boundary, validates their contracts, writes immutable explicit-schema
Parquet to the private `fintech-silver` bucket, and tracks each input in a SQLite processing
manifest. DuckDB is not required for the current object-local volume; Spark, Flink, Airflow, Gold,
reconciliation, warehouse loading, and dashboards remain out of scope.

```text
CDC Bronze Parquet -> validation -> normalize/deduplicate -> history
                                                       |-> latest_all -> current
                                                       |-> rejections/unresolved references

Settlement Bronze CSV -> settlement-v1 validation -> settlement Parquet + rejections
```

## Run and lineage model

One run processes one Bronze object. `run_id` separates immutable attempts; input URI/checksum,
code version, source schema, output URIs/counts, quality counts, and status remain in the manifest.
The identity `pipeline + input checksum + code version + Silver schema version` is never started a
second time unless `--force-reprocess` is explicit. A prior failed or in-progress identity is
returned with its existing non-success status, so automation cannot hide the incident. A force run
receives a new ID, rebuilds from the latest completed snapshot produced by a different input, and
preserves the earlier lineage. Dry-run creates neither manifest state nor output.

Lifecycle:

```text
DISCOVERED -> READING -> VALIDATING -> TRANSFORMING -> WRITING -> COMPLETED
      |          |           |              |            |
      +----------+-----------+--------------+----------> FAILED
                 +------------------------------------> QUARANTINED
```

Every required output is uploaded and checksum-verified before `COMPLETED`. Objects from a failed
partial run remain immutable under that run ID but are not publishable because no completed
manifest references them.

## CDC ordering and state

History preserves the structurally valid event grain. Deduplication first uses
`topic + partition + offset`, then deterministic event ID. Entity state uses Kafka offset within the
same topic-partition; source LSN and source/connector/ingestion clocks remain audit columns and
deterministic tie-break context, never timestamp-only ordering.

The design relies on Debezium keys keeping one business key on one Kafka partition. A key observed
on another partition is classified `OUT_OF_ORDER_EVENT` instead of silently merged. Each new object
merges into the latest completed `latest_all` snapshot. `current` is a filtered projection where
`is_deleted=false`.

- `r/c/u` apply `after`.
- `d` applies `before`, marks the latest row deleted, and remains in history.
- Tombstone keeps its history metadata and advances deletion lineage using the last valid payload.
- Missing-key records go to rejection and do not enter history or state.
- Transaction events use an append-only `events` output rather than current-state collapse.

## References and quality

Missing required reference fields are invalid. A syntactically present reference not yet present in
the latest current snapshot is non-blocking and written as `TEMPORARILY_UNRESOLVED`; this supports
late arrival without pretending referential completeness. Quality evidence contains coordinates or
row numbers, not full PII payloads, and is classified confidential.

## Storage

The `fintech-silver` bucket is bootstrapped idempotently with anonymous access disabled. Local mode
maps the same logical bucket to `data/silver`. Paths carry entity/date/run lineage and are immutable;
same key/same checksum is idempotent and different content is a collision.

## Known limitations

- SQLite is single-host control state without distributed leases.
- Current-state carry-forward is a sequence of immutable snapshots, not an ACID table format.
- Cross-partition keys are rejected; no global sequence service exists.
- Schema evolution is explicit-version rejection, not a registry/compatibility service.
- Reference checks use completed Silver snapshots and may remain unresolved until a later run.
- No compaction, retention, metrics/alerts, distributed compute, or orchestrated scheduling.
