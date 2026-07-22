# Shared Bronze Storage Abstraction

## Status and intent

Implemented in Phase 3 for settlement raw and quarantine artifacts and reused without changing its
immutable contract by Phase 5 CDC Bronze and Phase 6 Silver publication. Validation, discovery, the
SQLite manifest, and orchestration depend on `SettlementStorage`, not on the MinIO SDK. A small
shared `StorageBackend` supplies immutable file/byte writes, existence/stat/read operations, and
backend-neutral integrity metadata.

```text
SettlementIngestor
  -> SettlementStorage (domain layout)
       -> StorageBackend
            |-- LocalStorageBackend
            `-- MinioStorageBackend
```

The local adapter is the default for fast development and Docker-independent tests. The MinIO
adapter is selected with `STORAGE_BACKEND=minio` or `--storage-backend minio`; no validation rule or
manifest transition changes with that selection.

## Logical buckets and URIs

| Purpose | MinIO bucket | Local mapping | Manifest value |
| --- | --- | --- | --- |
| Raw accepted file | `fintech-bronze` | `data/bronze` | local path or `s3://fintech-bronze/<key>` |
| Invalid file/rejected rows | `fintech-quarantine` | `data/quarantine` | local path or `s3://fintech-quarantine/<key>` |
| Typed Silver outputs | `fintech-silver` | `data/silver` | local path or `s3://fintech-silver/<key>` |

Buckets remain private. The `s3://` convention describes the S3-compatible object identity; it
does not expose credentials or an authenticated endpoint.

## Object layouts

Bronze is content-addressed:

```text
settlements/
  partner_id=<partner>/
  settlement_date=YYYY-MM-DD/
  ingestion_date=YYYY-MM-DD/
  checksum=<sha256>/
  <source_file_name>
```

Quarantine is run-addressed so raw file evidence and rejected JSON Lines from independent attempts
cannot overwrite each other:

```text
settlements/
  partner_id=<partner>/
  settlement_date=<date-or-unknown>/
  ingestion_run_id=<run_id>/
  <source_file_name>[.rejected.jsonl]
```

## Immutable semantics

1. Compute or verify SHA-256 before publication.
2. Inspect the destination key.
3. If absent, create with `If-None-Match: *`; MinIO writes use a single SDK client with bounded
   timeout/retry settings.
4. Verify stored size and `checksum-sha256` metadata after upload.
5. Same key and checksum is an idempotent success.
6. Same key and a missing/different checksum raises `ImmutableCollisionError`; it is never silently
   overwritten.
7. The manifest reaches `PROCESSED` only after every required Bronze/quarantine write succeeds.

Local writes use an exclusive temporary file plus an atomic hard-link create. A sidecar
`.metadata.json` retains the same integrity and allowlisted metadata available as MinIO headers.

## Metadata boundary

Allowed metadata is: source name, partner, schema version, SHA-256, ingestion run, source file name
and size, counts, artifact type, and UTC ingestion time. Newlines are removed and values are bounded.
Unknown fields, secrets, authenticated URLs, and absolute local paths are discarded.

## Failure model and limitations

- SDK/network errors become explicit storage errors after bounded attempts; they are not swallowed.
- A raw invalid file is quarantine-only. A partially valid file writes raw Bronze plus rejected
  JSONL. If a required write fails, manifest state is `FAILED`, never `PROCESSED`.
- SQLite remains the transactional control store; MinIO is not used for mutable workflow state.
- Phase 3 is single-node development infrastructure. Distributed locks, bucket versioning, object
  lock/WORM retention, TLS, KMS encryption, scoped service accounts, replication, and lifecycle
  policies are planned hardening, not implemented claims.
- Kafka/Debezium and Silver now reuse the boundary. Airflow, Snowflake execution, dashboards, and
  observability remain out of scope.
