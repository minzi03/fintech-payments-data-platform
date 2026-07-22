# Local CDC Consumer Runbook

## Status

Implemented in Phase 5 for local development and opt-in integration testing. It consumes only the
six explicit `fintech.cdc.payments.<table>` topics, writes immutable Parquet to private Bronze, and
uses MinIO quarantine for poison records. Silver processing is not part of this runbook.

## Prerequisites

1. Copy `.env.example` to ignored `.env` and replace local placeholders.
2. Install Python 3.11+ dependencies with `pip install -e ".[dev]"`.
3. Start PostgreSQL, MinIO, Kafka, Connect, and the connector:

```bash
make minio-up
make cdc-up
make cdc-status
```

Verify `minio` and `kafka` are healthy and the Debezium connector/task are `RUNNING` before starting
the consumer. The default buckets are `fintech-bronze` and `fintech-quarantine` and remain private.

## Run

Bounded demonstration using host Python:

```bash
python -m ingestion.cdc_consumer.cli run --storage-backend minio --once
```

Long-running process:

```bash
make cdc-consumer-run
```

The Compose service is profile gated and therefore does not start with the core stack:

```bash
docker compose --env-file .env --profile cdc-consumer up -d --build cdc-consumer
make cdc-consumer-logs
```

Useful bounded overrides are `--topics`, `--group-id`, `--batch-size`, `--flush-interval`,
`--max-messages`, and `--once`. `--dry-run` parses/batches only: it uploads nothing and commits no
offset. Local storage remains available through `--storage-backend local` for lightweight tests.

## Inspect safely

```bash
make inspect-cdc-bronze
python -m ingestion.cdc_consumer.cli inspect \
  --storage-backend minio \
  --object-uri s3://fintech-bronze/cdc/.../batch_id=....parquet
```

Inspection prints manifest coordinates, status, counts, checksums, schema metadata, and operation
counts. It deliberately excludes record keys, `before`, `after`, and raw event fields.

For direct MinIO verification:

```bash
docker compose --env-file .env exec minio-init \
  mc find local/fintech-bronze/cdc --name '*.parquet'
```

Compare the object `checksum-sha256` metadata to a SHA-256 of the downloaded exact bytes.

## Tests

```bash
make test-cdc-consumer-unit
RUN_CDC_CONSUMER_INTEGRATION=1 pytest -m cdc_consumer_integration
```

The integration suite requires healthy Kafka and MinIO, publishes controlled `r/c/u/d`, tombstone,
and poison probes, validates Parquet/checksums/metadata, checks committed offsets, restarts the same
group, and exercises the upload-before-commit crash window. It is opt-in locally; unit tests remain
in the default CI quality job.

## Shutdown and reset

SIGINT/SIGTERM stops new polls, flushes valid pending partitions, uploads/verifies, commits their
offsets, removes temporary files, and closes the client. Allow at least
`CDC_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS` before forced termination.

Destructive development reset is guarded:

```bash
make reset-cdc-consumer-state CONFIRM=1
```

It stops only `cdc-consumer`, deletes only the named consumer group and the two dedicated consumer
state/temp volumes. It does not delete PostgreSQL, Kafka, or MinIO volumes or Bronze objects. Because
objects remain immutable, rerunning from reset offsets is idempotent for identical ranges and fails
explicitly on a checksum collision.

## Troubleshooting

- `UPLOADED`, not `COMMITTED`: check broker connectivity and use the recovery runbook; do not delete
  the object.
- Repeated poison offset: verify the quarantine bucket is reachable/private and inspect only its safe
  error metadata.
- Immutable collision: stop the consumer and investigate coordinate/schema/input differences. Never
  overwrite or delete the object as an automatic retry.
- Max poll/rebalance churn: reduce batch size, increase max poll interval within validated bounds,
  and inspect upload latency.
- SQLite lock errors: only one local writer is supported; distributed workers require a later
  PostgreSQL control-store migration.

## Limitations

Local Kafka is single broker/plaintext, MinIO uses local credentials without TLS/KMS/object lock,
SQLite has no distributed lease, and there are no metrics/alerts or hosted CDC integration job.
Airflow, Spark/Flink, Silver, dbt, Snowflake, reconciliation, dashboards, and observability remain
out of scope.
