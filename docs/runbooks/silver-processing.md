# Silver Processing Runbook

## Prerequisites

Install development dependencies and configure an ignored `.env`. For MinIO, ensure the three
private buckets exist:

```bash
make minio-up
docker compose --env-file .env up minio-init
```

`fintech-bronze` is input, `fintech-silver` is output, and `fintech-quarantine` remains ingestion/DLQ
evidence. Local mode uses `data/bronze`, `data/silver`, and the same code paths without Docker.

## Process CDC

```bash
python -m processing.silver.cli process-cdc \
  --storage-backend minio --input-prefix cdc/ --max-objects 10

python -m processing.silver.cli process-cdc \
  --storage-backend minio --input-object s3://fintech-bronze/cdc/.../batch.parquet
```

Filters include `--entity`, `--from-date`, `--to-date`, and `--max-objects`. `--dry-run` validates
without manifest/output writes. `--force-reprocess` creates a new run and output lineage even when
the input identity already completed.

## Process settlements

```bash
python -m processing.silver.cli process-settlements \
  --storage-backend minio --input-prefix settlements/
```

This reuses `contracts/batch/settlement_v1.yml`, emits accepted typed rows and confidential quality
records, and does not compare with internal payment transactions.

GNU Make equivalents:

```bash
make silver-process-cdc
make silver-process-settlements
make silver-process-once
```

## Inspect safely

```bash
make silver-inspect
python -m processing.silver.cli inspect --storage-backend minio \
  --object-uri s3://fintech-silver/silver/cdc/current/...
```

Inspection prints schemas, row/deleted/error counts, checksums and lineage status; it does not print
email, names, before/after JSON, financial references, or raw rejection payloads.

## Tests

```bash
make test-silver-unit
RUN_SILVER_INTEGRATION=1 pytest -m silver_integration
```

The integration suite uses real MinIO to exercise all six CDC entities, Decimal/UTC Parquet,
append-only transaction events, delete/latest/current semantics, rejection output, settlement
normalization, skip/force lineage, and immutable object verification.

## Local state reset

```bash
make reset-silver-state CONFIRM=1
```

This removes only the local Silver SQLite manifest. It does not remove Bronze, Silver objects,
Kafka offsets, PostgreSQL, MinIO volumes, or other manifests. Because output objects remain, a reset
followed by a new random run creates new lineage rather than overwriting previous evidence.
