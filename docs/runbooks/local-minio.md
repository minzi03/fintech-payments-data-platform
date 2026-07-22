# Local MinIO Runbook

## Scope

Phase 3 runs a single private MinIO service and an idempotent one-shot `minio-init` service. It
creates private `fintech-bronze`, `fintech-quarantine`, and `fintech-silver` buckets; PostgreSQL
remains the only other long-running
Compose service. This runbook is for local development, not production deployment.

## Configure

Copy `.env.example` to untracked `.env` and replace the clearly marked local placeholders. Required
client settings are:

```text
MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_SECURE, MINIO_REGION
MINIO_BRONZE_BUCKET, MINIO_QUARANTINE_BUCKET, MINIO_SILVER_BUCKET
MINIO_API_PORT, MINIO_CONSOLE_PORT
MINIO_CONNECT_TIMEOUT_SECONDS, MINIO_READ_TIMEOUT_SECONDS, MINIO_MAX_RETRIES
STORAGE_BACKEND
```

Do not commit `.env`, place credentials in a command line, or paste an authenticated endpoint into
logs. `MINIO_ENDPOINT` contains only host and optional port. Local Compose is HTTP by default, so
`MINIO_SECURE=false`; production TLS is not represented here.

## Start and verify

```bash
make minio-up
docker compose --env-file .env ps minio
docker compose --env-file .env run --rm minio-init
curl --fail http://localhost:${MINIO_API_PORT}/minio/health/ready
```

`minio-init` can be rerun: bucket creation uses `--ignore-existing`, and anonymous access is set to
`none` each time. Open the console at `http://localhost:${MINIO_CONSOLE_PORT}` only for local manual
inspection.

List private buckets and uploaded objects without printing credentials:

```bash
docker compose --env-file .env run --rm --entrypoint /bin/sh minio-init -c \
  'mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null && mc ls local && mc find local/fintech-bronze'
```

## Upload settlement data

```bash
make generate-settlement-fixtures
make ingest-settlements-minio
```

Equivalent CLI:

```bash
python -m src.ingestion.batch.cli ingest-settlements \
  --storage-backend minio \
  --input-dir data/inbound/settlements \
  --partner-id VCB \
  --contract contracts/batch/settlement_v1.yml
```

The ingestion and Silver SQLite manifests remain under `data/control/` and record credential-free
`s3://` URIs. Inbound files are not
deleted. Replaying an already successful checksum returns the existing manifest record and does not
create another object. Changed content receives a new checksum path.

## Tests and integrity verification

```bash
make test-minio-integration
```

The opt-in suite checks bucket bootstrap, raw byte equality, SHA-256 metadata, partial/file-level
quarantine, URI persistence, replay, collision safety, and failed-upload manifest semantics. A
manual download can be hashed with `sha256sum` (or PowerShell `Get-FileHash -Algorithm SHA256`) and
compared with the manifest/object metadata.

## Stop and reset

```bash
make minio-down
```

Stopping/removing containers preserves `fintech-payments-minio-data`. Reset is destructive and has
an explicit guard:

```bash
make minio-reset CONFIRM=1
```

This deletes only the named MinIO volume, restarts MinIO, and recreates the private buckets. It does
not delete PostgreSQL data or local inbound/control files.

## Failure handling

- Unhealthy service: inspect `make minio-logs`, endpoint ports, and local `.env` consistency.
- Bootstrap failure: rerun `minio-init` after MinIO is healthy; the operation is idempotent.
- Manifest `FAILED`: restore storage availability and replay the inbound file. No `PROCESSED` state
  was written for the failed upload.
- Immutable collision: do not delete/overwrite automatically. Compare checksum and key construction,
  preserve evidence, and reset only a disposable local environment with the guarded command.

High availability, TLS certificates, external secrets, least-privilege service accounts, retention,
object lock, backup/replication, metrics, and alerting are planned and out of scope for Phase 3.
