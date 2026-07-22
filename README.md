# Fintech Payments Data Platform

A production-like data platform for a hypothetical fintech providing payment gateway, merchant
payments, account-to-account transfers, refunds, and banking-partner settlement.

Long-term business use cases:

1. Near-real-time payment operations monitoring.
2. Daily reconciliation between internal payments and partner settlement files.

## Project status

**Current phase: Phase 2 - Banking Partner Settlement Batch Ingestion Foundation**

Implemented:

- Phase 0 repository standards, documentation, tests, CI, and safe configuration.
- Phase 1 PostgreSQL 16 OLTP source and deterministic payment-domain generator.
- A versioned `settlement-v1` CSV contract with Decimal, timestamp, naming, business-key, and quality
  rules.
- Deterministic settlement scenario fixtures.
- A Python batch service with SHA-256 identity, SQLite manifest lifecycle, immutable local Bronze,
  file/record validation, partial rejection, quarantine, dry-run, and structured results.
- Docker-independent batch unit and local filesystem/SQLite integration tests.

Kafka, Debezium, MinIO server, Airflow, Spark, executable dbt models, Snowflake, dashboards, and
observability are not implemented. Phase 2 performs no reconciliation.

## Implemented data flow

```text
Payment generator --------------------------> PostgreSQL OLTP

Partner settlement CSV
        |
        v
filename + SHA-256 + settlement-v1 validation
        |
        +--> SQLite manifest/control state
        +--> local Bronze (unaltered raw CSV + metadata)
        `--> local quarantine (invalid file or rejected rows)
```

## Repository map

| Path | Responsibility |
| --- | --- |
| `contracts/batch/` | Versioned partner settlement file contracts. |
| `src/ingestion/batch/` | Discovery, contract loading, validation, manifest, storage, fixtures, orchestration, and CLI. |
| `data/` | Ignored local inbound, Bronze, quarantine, and control runtime data. |
| `src/common/` | Environment configuration, database lifecycle, and logging. |
| `src/generators/` | Phase 1 deterministic PostgreSQL domain generator. |
| `infrastructure/postgres/init/` | Phase 1 OLTP schema, reference data, and indexes. |
| `tests/unit/` | Docker-independent unit tests. |
| `tests/integration/batch/` | Local filesystem and SQLite batch integration tests. |
| `docs/` | Business context, contracts, architecture, roadmap, and runbooks. |

## Setup

Python 3.11 or newer is required. PostgreSQL/Docker is optional for settlement-only work.

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

GNU Make equivalents:

```bash
make generate-settlement-fixtures
make ingest-settlements
```

See the [settlement ingestion runbook](docs/runbooks/settlement-batch-ingestion.md) for manifest
states, replay, object layout, and cleanup.

## PostgreSQL source

The Phase 1 source remains unchanged:

```bash
docker compose up -d --wait postgres
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250"
```

See the [local PostgreSQL runbook](docs/runbooks/local-postgres.md).

## Quality checks

```bash
ruff check .
ruff format --check .
pytest -m "not integration"
pytest -m batch_integration
python -m yamllint .
docker compose --env-file .env.example config --quiet
```

PostgreSQL integration tests remain opt-in through `TEST_DATABASE_URL`. `make validate` keeps the
fast unit/quality gate; `make test-batch-integration` runs Phase 2 integration tests.

## Documentation

- [Business case](docs/business/business-case.md)
- [Requirements](docs/business/requirements.md)
- [OLTP schema](docs/data-model/oltp-schema.md)
- [Settlement contract](docs/data-model/settlement-contract.md)
- [Source model](docs/data-model/source-model.md)
- [Settlement batch runbook](docs/runbooks/settlement-batch-ingestion.md)
- [Roadmap](docs/roadmap.md)

## Security baseline

- No credentials are required for local batch ingestion.
- PostgreSQL credentials remain environment variables and are never logged in full.
- No card data, customer name, national ID, bank credential, or authentication token belongs in the
  settlement contract.
- Rejected-record evidence contains source financial references and must be treated as confidential.
- Production encryption, secret management, role separation, transport security, and retention are
  future hardening work.
