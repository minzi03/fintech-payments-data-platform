# Settlement Batch Ingestion Runbook

## Scope

This runbook operates the settlement Python service through Phase 3. SQLite remains the control
store while immutable Bronze/quarantine artifacts use either the default local filesystem or private
MinIO buckets. It does not perform reconciliation or orchestrate a schedule.

## Prerequisites

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
```

Runtime paths default to:

```text
data/
|-- inbound/settlements/
|-- bronze/settlements/
|-- quarantine/settlements/
`-- control/settlement_manifest.sqlite3
```

The whole `data/` directory is ignored by Git.

## Generate deterministic fixtures

```bash
python -m src.ingestion.batch.cli generate-settlement-fixtures \
  --output-dir data/inbound/settlements \
  --partner-id VCB \
  --settlement-date 2026-07-22 \
  --seed 42
```

Equivalent Make target:

```bash
make generate-settlement-fixtures
```

The generator creates valid reconciliation candidates, duplicate/invalid rows, invalid schema,
empty-file, and same-name-changed-content fixtures. Re-running with the same arguments produces the
same bytes.

## Ingest one file

```bash
python -m src.ingestion.batch.cli ingest-settlements \
  --file data/inbound/settlements/settlement_VCB_2026-07-22_001.csv \
  --partner-id VCB \
  --contract contracts/batch/settlement_v1.yml
```

Ingest every regular file directly under an input directory:

```bash
python -m src.ingestion.batch.cli ingest-settlements \
  --input-dir data/inbound/settlements \
  --partner-id VCB \
  --contract contracts/batch/settlement_v1.yml
```

`--file` and `--input-dir` are mutually exclusive. Inbound files are retained.

## Select storage backend

Local storage is the default and needs no Docker:

```bash
python -m src.ingestion.batch.cli ingest-settlements ... --storage-backend local
```

For MinIO, start/bootstrap the service, configure untracked `.env`, and select the adapter:

```bash
make minio-up
python -m src.ingestion.batch.cli ingest-settlements ... --storage-backend minio
```

`STORAGE_BACKEND` provides the CLI default. The explicit flag wins. Contract loading, validation,
manifest identity, and status ordering are identical for both backends. See
[Local MinIO](local-minio.md) for configuration, bucket verification, reset, and integration tests.

## Validation modes

Dry-run performs filename, checksum, file-level, and row-level validation without creating manifest,
Bronze, or quarantine artifacts:

```bash
python -m src.ingestion.batch.cli ingest-settlements ... --dry-run
```

Default mode permits partial row acceptance. Strict mode quarantines any file containing an invalid
record and does not write it to Bronze:

```bash
python -m src.ingestion.batch.cli ingest-settlements ... --fail-on-rejected-records
```

## Manifest lifecycle

```text
DISCOVERED -> VALIDATING -> VALIDATED -> PROCESSING -> PROCESSED
                      |          |            |
                      +----------+------------+-> QUARANTINED
                      +----------+------------+-> FAILED -> VALIDATING (retry)
```

The SQLite manifest records deterministic `file_id`, source/name/path, partner/date, byte size,
SHA-256, schema version, timestamps, counts, errors, Bronze/quarantine paths, and ingestion run ID.
`PROCESSED` is written only after the raw Bronze copy and any rejected-record artifact succeed.
Local artifacts use filesystem paths; MinIO artifacts use credential-free `s3://bucket/key` URIs.

## Idempotency behavior

| Discovery | Behavior |
| --- | --- |
| Same name, same bytes | Return existing terminal manifest as skipped |
| Different valid name, same bytes | Return checksum duplicate as skipped |
| Same name, changed bytes | Register a new content/file ID and process independently |
| Prior `FAILED` content | Retry validation and immutable writes using the same file ID |
| Prior `QUARANTINED` content | Return existing quarantine result as skipped |

Bronze object key (under `data/bronze` locally or `fintech-bronze` in MinIO):

```text
settlements/
  partner_id=<partner>/
  settlement_date=YYYY-MM-DD/
  ingestion_date=YYYY-MM-DD/
  checksum=<sha256>/
  <original_file_name>
```

The local adjacent `.metadata.json` sidecar and MinIO user metadata record an allowlisted checksum,
source name/file name and size, ingestion time, schema version, row counts, partner, artifact type,
and run ID. Absolute paths and credentials are excluded. Raw CSV bytes are never normalized.

Quarantine is keyed by partner, settlement date (or `unknown`), and `ingestion_run_id`. Invalid file
schema/naming writes only the raw quarantine object. Partial record rejection writes unchanged raw
Bronze plus `.rejected.jsonl` in quarantine.

## Replay procedure

1. Inspect the structured CLI result and manifest error fields.
2. Correct external storage availability or provide changed source content as appropriate.
3. Retry the same command. A `FAILED` checksum resumes safely; a `PROCESSED` checksum is skipped.
4. Never edit a Bronze object. A source correction must arrive as new content and receives a new
   deterministic file ID.

## Tests

```bash
make test-batch-unit
make test-batch-integration
make test-minio-integration

# Direct commands
pytest tests/unit/ingestion/batch
pytest -m batch_integration
RUN_MINIO_INTEGRATION=1 pytest -m minio_integration
```

Batch unit/local integration tests require no Docker. MinIO integration tests are opt-in and require
healthy bootstrapped MinIO; no PostgreSQL is involved.

## Destructive runtime cleanup

**Warning: this deletes local inbound fixtures, Bronze evidence, quarantine evidence, and the SQLite
manifest. It does not affect PostgreSQL.**

```bash
make clean-runtime-data CONFIRM=1
```

## Known limitations

- Single-process local SQLite control store; no distributed lease or multi-worker claim.
- Single-node MinIO only; no distributed locking, object lock/versioning, replication, lifecycle,
  scoped service identities, TLS, or external secret manager.
- No partner transport/SFTP polling, encryption-at-rest integration, malware scanning, PGP, or
  retention automation.
- No cross-file business-key deduplication beyond checksum and name/content history.
- No reconciliation or lookup against PostgreSQL.
- No Airflow schedule, monitoring service, dashboard, or production alerting.
