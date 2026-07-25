# Fintech Payments Data Platform

A production-like data platform for a hypothetical fintech providing payment gateway, merchant
payments, account-to-account transfers, refunds, and banking-partner settlement.

Long-term business use cases:

1. Near-real-time payment operations monitoring.
2. Daily reconciliation between internal payments and partner settlement files.

## Project status

**Implementation baseline: Phase 7 complete**

**Production-readiness phase: Design Freeze in progress**

The Phase 0-7 local platform is executable and tested. It is not approved for a production pilot.
The first production blocker, dataset bootstrap and atomic activation, is frozen in ADR-001
Revision 4. ADR-002 through ADR-005 still require architecture review before remediation code may
start.

- [Design Freeze status](docs/design-freeze.md)
- [Production Readiness Backlog](docs/production-readiness-backlog.md)
- [Accepted ADR-001](docs/adr/001-dataset-bootstrap-and-atomic-activation.md)
- [ADR-001 final design review](docs/design-reviews/adr-001-design-freeze-review-v2.md)
- [Enterprise Data Platform Portal target design](docs/product/enterprise-data-platform-portal.md)

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
- Apache Airflow 3.3 with LocalExecutor, dedicated metadata PostgreSQL, a least-privilege `control`
  schema, four bounded DAGs, retries/timeouts, aggregate quality gates, manual backfill, and
  idempotent orchestration of the existing batch/CDC/Silver applications.
- PR-PORTAL-001 Enterprise Data Platform Portal foundation with an independently deployable
  Next.js shell, FastAPI BFF, versioned OpenAPI contract, generated TypeScript client, truthful
  health/readiness, correlation, Problem Details, and isolated Docker startup.

Spark/Flink, executable dbt models, Snowflake, dashboards, Gold reconciliation, and a full
observability platform are not implemented. Empty runtime/package scaffolds for those planned
phases are intentionally not shipped; they will be introduced with executable behavior and tests.
The Enterprise Data Platform Portal currently ships only its technical foundation. It is not yet a
complete operational UI: authentication, authorization, inventories, infrastructure adapters, and
mutations remain explicitly disabled and deferred.

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
                                                                  |
                                                                  v
                                              Airflow schedules/control + PostgreSQL control schema

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
| `src/orchestration/` | Airflow-neutral control store, health checks, quality gates, and application adapters. |
| `airflow/dags/` | Four Phase 7 DAG definitions; no business transformation logic. |
| `infrastructure/airflow/` | Pinned Airflow image and versioned control-schema DDL. |
| `apps/portal-api/` | FastAPI Portal BFF foundation, health, adapters, errors, telemetry, and tests. |
| `apps/portal-web/` | Next.js Portal shell, System Status, generated-client integration, and tests. |
| `packages/portal-contracts/` | Checked-in OpenAPI and generated TypeScript API client. |
| `docs/portal/` | Portal boundaries, contract, configuration, local development, testing, and troubleshooting. |
| `infrastructure/cdc-consumer/` | Profile-gated pinned Python consumer image. |
| `tests/unit/` | Docker-independent unit tests. |
| `tests/integration/batch/` | Local filesystem and SQLite batch integration tests. |
| `tests/integration/minio/` | Opt-in real MinIO storage and ingestion integration tests. |
| `tests/integration/cdc/` | Opt-in PostgreSQL/Kafka/Debezium end-to-topic acceptance tests. |
| `tests/integration/cdc_consumer/` | Opt-in real Kafka-to-MinIO Parquet/recovery acceptance tests. |
| `docs/adr/` | Versioned production-readiness architecture decisions. |
| `docs/design-reviews/` | Architecture-only freeze evidence and verdicts. |
| `docs/product/` | Target product architecture; no Portal runtime implementation. |
| `docs/` | Business context, contracts, architecture, roadmap, runbooks, and readiness backlog. |

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

## Enterprise Data Platform Portal foundation

The Portal is a control-plane client with one guarded interaction path:

```text
Browser -> Next.js Portal Web -> FastAPI Portal API -> explicit versioned adapters
```

PR-PORTAL-001 exposes foundation health and safe build metadata only. It does not connect the
browser to PostgreSQL, Kafka, Kafka Connect, MinIO, Airflow, or any control database, and it does
not present planned operations as available.

```bash
make portal-install
make portal-contracts
make portal-up
```

Open:

- Portal Web: <http://localhost:3000>
- System Status: <http://localhost:3000/system-status>
- Portal API health: <http://localhost:8010/health/live>
- Development API documentation: <http://localhost:8010/docs>

Validate with `make portal-test`, `make portal-contract-check`, and `make portal-e2e`; stop with
`make portal-down`. See [Portal local development](docs/portal/local-development.md) and
[architecture boundaries](docs/portal/architecture-boundaries.md).

## Generate settlement fixtures

```bash
python -m ingestion.batch.cli generate-settlement-fixtures \
  --output-dir data/inbound/settlements \
  --partner-id VCB \
  --settlement-date 2026-07-22 \
  --seed 42
```

## Ingest settlements

```bash
python -m ingestion.batch.cli ingest-settlements \
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
python -m ingestion.batch.cli ingest-settlements \
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

## Airflow orchestration

After replacing Airflow secret placeholders in ignored `.env`:

```bash
make airflow-build
make airflow-init
make airflow-up
make airflow-dags-list
```

Airflow runs the settlement pipeline, a bounded CDC health/control DAG, dependency-aware CDC Silver
processing, and a manual validated backfill. The long-running CDC consumer remains outside Airflow.
Central PostgreSQL control state tracks pipeline/task aggregates and quality results while the
three existing SQLite manifests remain fine-grained sources of truth.

See [orchestration architecture](docs/architecture/orchestration.md), the
[control-plane boundary](docs/architecture/control-plane.md), and the
[local Airflow runbook](docs/runbooks/airflow-local.md).

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
- [Current architecture state](docs/architecture/current-state.md)
- [Production Readiness Backlog](docs/production-readiness-backlog.md)
- [Design Freeze status](docs/design-freeze.md)
- [Architecture Decision Records](docs/adr/README.md)
- [Design Review process and evidence](docs/design-reviews/)
- [Enterprise Data Platform Portal](docs/product/enterprise-data-platform-portal.md)
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
