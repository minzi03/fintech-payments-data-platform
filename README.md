# Fintech Payments Data Platform

A production-like data platform for a hypothetical fintech providing payment gateway, merchant
payments, account-to-account transfers, refunds, and banking-partner settlement.

Long-term business use cases:

1. Near-real-time payment operations monitoring.
2. Daily reconciliation between internal payments and partner settlement files.

## Project status

**Current phase: Phase 6 - Bronze-to-Silver Processing Foundation**

Implemented:

- Phase 0 repository standards, documentation, tests, CI, and safe configuration.
- Phase 1 PostgreSQL 16 OLTP source and deterministic payment-domain generator.
- A versioned `settlement-v1` CSV contract with Decimal, timestamp, naming, business-key, and quality
  rules.
- Deterministic settlement scenario fixtures.
- A Python batch service with SHA-256 identity, SQLite manifest lifecycle, immutable local Bronze,
  file/record validation, partial rejection, quarantine, dry-run, and structured results.
- A storage interface with local filesystem and MinIO adapters, private bucket bootstrap, immutable
  conditional writes, checksummed metadata, bounded retries, and collision protection.
- PostgreSQL logical replication with a dedicated non-superuser CDC role, explicit six-table
  publication, Kafka 4 KRaft broker, Debezium Kafka Connect, idempotent connector bootstrap, and
  schema-enabled CDC topics.
- Bounded metadata-only topic inspection plus opt-in integration coverage for initial snapshots,
  inserts, updates, delete/tombstone behavior, exact Decimals, timestamps, LSNs, and restart safety.
- A Python `confluent-kafka` consumer with auto commit/store disabled, partition-aware contiguous
  micro-batches, explicit-schema ZSTD Parquet, deterministic event/batch/object identity, SQLite
  batch manifest, upload-before-commit recovery, and private MinIO poison quarantine.
- Docker-independent unit/local batch tests and opt-in integration tests against real Kafka/MinIO.
- A Python/PyArrow Bronze-to-Silver CLI with incremental discovery, explicit entity schemas,
  Decimal/UTC normalization, CDC history/latest/current, contract-based settlement projection,
  quality outputs, immutable Silver publication, and processing lineage.

Airflow, Spark/Flink, executable dbt models, Snowflake, dashboards, Gold reconciliation, and an
observability platform are not implemented.

## Implemented data flow

```text
Payment generator --------------------------> PostgreSQL OLTP
                                                    |
                                                    v
                                    logical WAL -> Debezium -> Kafka CDC topics
                                                                  |
                                                                  v
                                         partition micro-batch -> Parquet
                                                   |                   |
                                                   v                   v
                                         SQLite batch manifest   MinIO Bronze
                                                                  |
                                             poison record -------+--> MinIO quarantine
                                                                  |
                                                                  v
                                         PyArrow Silver processing -> MinIO Silver

Partner settlement CSV
        |
        v
filename + SHA-256 + settlement-v1 validation
        |
        +--> SQLite manifest/control state
        +--> storage interface --> local or MinIO Bronze (unaltered raw CSV + metadata)
        `--> storage interface --> local or MinIO quarantine (invalid file or rejected rows)
```

## Repository map

| Path | Responsibility |
| --- | --- |
| `contracts/batch/` | Versioned partner settlement file contracts. |
| `src/ingestion/batch/` | Discovery, contract loading, validation, manifest, storage, fixtures, orchestration, and CLI. |
| `data/` | Ignored local inbound, Bronze, quarantine, and control runtime data. |
| `src/common/` | Typed configuration, database lifecycle, logging, and shared immutable storage backends. |
| `src/generators/` | Phase 1 deterministic PostgreSQL domain generator. |
| `infrastructure/postgres/init/` | Phase 1 OLTP schema, reference data, and indexes. |
| `infrastructure/debezium/` | Versioned connector config and pinned bootstrap image. |
| `scripts/cdc/` | Least-privilege PostgreSQL bootstrap, connector lifecycle, and safe topic inspection. |
| `src/ingestion/cdc_consumer/` | Envelope parsing, batching, Parquet, manifest, storage, DLQ, recovery, Kafka loop, and CLI. |
| `src/processing/silver/` | Bronze read, normalization, state/quality, Parquet, lineage manifest, and CLI. |
| `infrastructure/cdc-consumer/` | Profile-gated pinned Python consumer image. |
| `tests/unit/` | Docker-independent unit tests. |
| `tests/integration/batch/` | Local filesystem and SQLite batch integration tests. |
| `tests/integration/minio/` | Opt-in real MinIO storage and ingestion integration tests. |
| `tests/integration/cdc/` | Opt-in PostgreSQL/Kafka/Debezium end-to-topic acceptance tests. |
| `tests/integration/cdc_consumer/` | Opt-in real Kafka-to-MinIO Parquet/recovery acceptance tests. |
| `docs/` | Business context, contracts, architecture, roadmap, and runbooks. |

## Setup

Python 3.11 or newer is required. Docker is optional for the default local storage backend.

```bash
python -m venv .venv
source .venv/bin/activate        # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env             # PowerShell: Copy-Item .env.example .env
```

`.env` and the entire `data/` runtime tree are ignored by Git.

## Generate settlement fixtures

```bash
python -m src.ingestion.batch.cli generate-settlement-fixtures \
  --output-dir data/inbound/settlements \
  --partner-id VCB \
  --settlement-date 2026-07-22 \
  --seed 42
```

## Ingest settlements

```bash
python -m src.ingestion.batch.cli ingest-settlements \
  --input-dir data/inbound/settlements \
  --partner-id VCB \
  --contract contracts/batch/settlement_v1.yml
```

Use `--file` for one file, `--dry-run` for validation without persistent writes, or
`--fail-on-rejected-records` for strict file quarantine. The default permits partial row rejection
while preserving the complete raw source in Bronze.

To use private MinIO buckets, put non-production local values in untracked `.env`, then run:

```bash
make minio-up
python -m src.ingestion.batch.cli ingest-settlements \
  --storage-backend minio \
  --input-dir data/inbound/settlements \
  --partner-id VCB \
  --contract contracts/batch/settlement_v1.yml
```

MinIO manifests store `s3://fintech-bronze/...` and `s3://fintech-quarantine/...` URIs. The source
bytes are unchanged; metadata headers contain only an explicit non-secret allowlist.

GNU Make equivalents:

```bash
make generate-settlement-fixtures
make ingest-settlements
make ingest-settlements-minio
```

See the [settlement ingestion runbook](docs/runbooks/settlement-batch-ingestion.md) for manifest
states and replay, and the [local MinIO runbook](docs/runbooks/local-minio.md) for object storage.

## PostgreSQL source

The Phase 1 source remains unchanged:

```bash
docker compose up -d --wait postgres
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250"
```

See the [local PostgreSQL runbook](docs/runbooks/local-postgres.md).

## PostgreSQL CDC to Kafka

Configure ignored `.env`, generate source rows before the first connector registration when you
want to exercise the initial snapshot, then start the bounded Phase 4 stack:

```bash
make postgres-up
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250"
make cdc-up
make cdc-status
make cdc-inspect CDC_TABLE=payment_transactions
```

The six CDC topics follow `fintech.cdc.payments.<table>`. JSON converters keep schemas and the full
Debezium envelope. `NUMERIC(18,2)` uses Kafka Connect Decimal bytes (`precise`), never a binary
floating-point representation. Inspection prints primary keys and operational metadata only, not
full customer/payment payloads. `make cdc-down` removes only connector/Kafka containers and retains
PostgreSQL, MinIO, and the Kafka volume.

See [CDC architecture](docs/architecture/cdc-architecture.md), the
[CDC event contract](docs/data-model/cdc-event-contract.md), and the
[local Kafka/Debezium runbook](docs/runbooks/local-kafka-debezium.md).

## Reliable CDC Bronze consumer

Run a bounded pass after Kafka, Connect, and MinIO are healthy:

```bash
python -m ingestion.cdc_consumer.cli run --storage-backend minio --once
```

The consumer subscribes only to the configured six-table allowlist. It stores one immutable object
per topic/partition/contiguous offset range, verifies its checksum, records `UPLOADED`, synchronously
commits `offset_end + 1`, and only then records `COMMITTED`. A replay reuses the same object when its
checksum agrees and fails rather than overwriting when it differs. Malformed records are written to
the private quarantine bucket before their source offsets advance.

```bash
make cdc-consumer-run
make cdc-consumer-once
make inspect-cdc-bronze
```

The Compose `cdc-consumer` service is behind the `cdc-consumer` profile, so core infrastructure does
not automatically start a long-running consumer. See [CDC Bronze architecture](docs/architecture/cdc-bronze-ingestion.md),
the [Bronze schema](docs/data-model/cdc-bronze-schema.md), and the
[consumer runbook](docs/runbooks/cdc-consumer.md).

## Bronze to Silver

Process bounded CDC or settlement Bronze objects with local or MinIO storage:

```bash
python -m processing.silver.cli process-cdc \
  --storage-backend minio --input-prefix cdc/ --max-objects 10
python -m processing.silver.cli process-settlements \
  --storage-backend minio --input-prefix settlements/
```

CDC produces immutable history, latest-all (including deletes), active current, append-only
transaction events, rejections, and unresolved-reference evidence. Settlement processing applies
`settlement-v1` and retains Decimal/UTC types; it does not reconcile. Completed inputs are skipped
unless `--force-reprocess`; dry-run writes no manifest or object.

See [Silver architecture](docs/architecture/silver-processing.md), the
[Silver data model](docs/data-model/silver-data-model.md), and the
[Silver runbook](docs/runbooks/silver-processing.md).

## Quality checks

```bash
ruff check .
ruff format --check .
pytest -m "not integration"
pytest -m batch_integration
RUN_MINIO_INTEGRATION=1 pytest -m minio_integration
RUN_CDC_INTEGRATION=1 pytest -m cdc_integration
RUN_CDC_CONSUMER_INTEGRATION=1 pytest -m cdc_consumer_integration
RUN_SILVER_INTEGRATION=1 pytest -m silver_integration
python -m yamllint .
docker compose --env-file .env.example config --quiet
```

PostgreSQL integration tests remain opt-in through `TEST_DATABASE_URL`. MinIO and CDC tests require
their healthy services and explicit run flags. `make validate` remains the fast default gate; real
infrastructure suites have dedicated targets.

## Documentation

- [Business case](docs/business/business-case.md)
- [Requirements](docs/business/requirements.md)
- [OLTP schema](docs/data-model/oltp-schema.md)
- [Settlement contract](docs/data-model/settlement-contract.md)
- [Source model](docs/data-model/source-model.md)
- [Settlement batch runbook](docs/runbooks/settlement-batch-ingestion.md)
- [Storage abstraction](docs/architecture/storage-abstraction.md)
- [CDC architecture](docs/architecture/cdc-architecture.md)
- [CDC event contract](docs/data-model/cdc-event-contract.md)
- [CDC Bronze ingestion architecture](docs/architecture/cdc-bronze-ingestion.md)
- [CDC Bronze Parquet schema](docs/data-model/cdc-bronze-schema.md)
- [CDC consumer runbook](docs/runbooks/cdc-consumer.md)
- [CDC recovery runbook](docs/runbooks/cdc-recovery.md)
- [Silver processing architecture](docs/architecture/silver-processing.md)
- [Silver data model](docs/data-model/silver-data-model.md)
- [Silver quality rules](docs/data-model/silver-quality-rules.md)
- [Silver processing runbook](docs/runbooks/silver-processing.md)
- [Silver recovery runbook](docs/runbooks/silver-recovery.md)
- [Local Kafka and Debezium runbook](docs/runbooks/local-kafka-debezium.md)
- [Local MinIO runbook](docs/runbooks/local-minio.md)
- [Roadmap](docs/roadmap.md)

## Security baseline

- No credentials are required for local batch ingestion; MinIO values come only from environment
  variables and secret-bearing configuration fields are excluded from representations.
- PostgreSQL credentials remain environment variables and are never logged in full.
- The Debezium role is separate from the application administrator, has replication plus explicit
  schema/table read grants, and is actively verified as non-superuser.
- Kafka and Kafka Connect bind only to loopback for local diagnostics; the inspection command
  redacts row payloads and never creates a durable consumer group.
- No card data, customer name, national ID, bank credential, or authentication token belongs in the
  settlement contract.
- Rejected-record evidence contains source financial references and must be treated as confidential.
- CDC Parquet and poison evidence are confidential; logs/inspection omit keys and row payloads.
- Silver and rejection Parquet are private/confidential; inspection exposes only schema, counts,
  state flags, lineage, and checksums.
- Buckets are private; anonymous access is explicitly disabled by bootstrap.
- Local Kafka/Connect traffic is plaintext. TLS/SASL, external secret management, ACLs, retention
  locking, and distributed deployment are future hardening work.
