# Fintech Payments Data Platform

A production-like data platform for a hypothetical fintech that provides a payment gateway,
merchant payments, account-to-account transfers, refunds, and settlement with banking partners.

The long-term platform serves two business use cases:

1. Near-real-time payment operations monitoring.
2. Daily reconciliation between internal transactions and partner settlement files.

## Project status

**Current phase: Phase 1 - PostgreSQL OLTP Source and Realistic Data Generator**

Implemented in this phase:

- A PostgreSQL 16 OLTP source with payment-domain tables, reference data, constraints, indexes,
  audit timestamps, and immutable transaction events.
- A typed, deterministic Python generator for customers, accounts, merchants, payment lifecycles,
  events, and refunds.
- Controlled invalid-amount and duplicate-idempotency probes that prove database constraints without
  leaving invalid rows behind.
- Unit tests that require no database and optional PostgreSQL integration tests.

Kafka, Debezium, MinIO, Airflow, Spark, dbt execution, Snowflake, BI, and observability remain planned
and are not deployed in Phase 1. Targets and scale figures remain assumptions until measured.

## Phase 1 data flow

```text
Deterministic Python generator
            |
            v
PostgreSQL OLTP (payments schema)
  |-- current customer/account/merchant/payment/refund state
  `-- immutable transaction lifecycle events
```

The broader target architecture is documented separately and must not be interpreted as running.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/common/` | Environment configuration, safe database connection handling, and logging. |
| `src/generators/` | Deterministic domain generation, persistence, and the Phase 1 CLI. |
| `infrastructure/postgres/init/` | Ordered PostgreSQL schema, reference-data, and index scripts. |
| `tests/unit/` | Docker-independent tests for configuration and domain rules. |
| `tests/integration/` | PostgreSQL schema, constraint, and generator persistence tests. |
| `docs/` | Business context, architecture, data models, runbooks, and roadmap. |
| `airflow/`, `dbt/`, `contracts/`, `dashboards/` | Reserved Phase 0 boundaries; no runtime implementation yet. |

## Local setup

Python 3.11 or newer, Docker Compose, and optionally GNU Make are required.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env             # PowerShell: Copy-Item .env.example .env
```

The example values are local-only placeholders. Change them in the ignored `.env` file; never commit
real credentials.

## Start PostgreSQL and generate data

```bash
docker compose config --quiet
docker compose up -d --wait postgres
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250 --invalid-rate 0.02 --duplicate-rate 0.02"
```

With Make:

`Makefile` loads `.env` when it exists and otherwise uses `.env.example`. Without GNU Make, export the
required database environment variables in the current shell, then run `python -m generators.cli`
with the same generator arguments.

`invalid-rate` and `duplicate-rate` run isolated constraint probes inside savepoints. Rejected probes
are counted, rolled back, and never pollute the generated business data.

See the [local PostgreSQL runbook](docs/runbooks/local-postgres.md) for health checks, logs,
troubleshooting, and the destructive reset procedure.

## Quality checks

```bash
ruff check .
ruff format --check .
pytest -m "not integration"
python -m yamllint .
docker compose config --quiet
```

PostgreSQL integration tests are explicit:

```bash
TEST_DATABASE_URL=postgresql://payments_app:change_me@localhost:5432/fintech_payments \
  pytest -m integration
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql://payments_app:change_me@localhost:5432/fintech_payments"
pytest -m integration
```

`make validate` preserves the fast default gate; `make test-integration` runs the database suite when
PostgreSQL is available.

## Documentation entry points

- [Business case](docs/business/business-case.md)
- [Business and platform requirements](docs/business/requirements.md)
- [Target architecture](docs/architecture/target-architecture.md)
- [Source model](docs/data-model/source-model.md)
- [Detailed OLTP schema](docs/data-model/oltp-schema.md)
- [Local PostgreSQL runbook](docs/runbooks/local-postgres.md)
- [Implementation roadmap](docs/roadmap.md)

## Security baseline

- Runtime credentials come only from environment variables or a future secret manager.
- The generator never logs passwords or a complete connection URL.
- `.env`, logs, runtime data, test caches, and database volumes are excluded from Git.
- National identifiers, card data, bank credentials, and other unnecessary sensitive values are not
  generated or stored.
- Phase 1 uses a local application role. Production role separation, rotation, TLS, masking, and
  retention controls remain future hardening work.
