# Fintech Payments Data Platform

A production-like data platform for a hypothetical fintech that provides a payment gateway,
merchant payments, transfers, refunds, and partner-bank settlement.

The platform is designed around two business-critical use cases:

1. Near-real-time payment operations monitoring.
2. Daily reconciliation between internal payment transactions and settlement files received from
   banking partners.

## Project status

**Current phase: Phase 0 - Project Foundation**

Phase 0 establishes the repository boundaries, engineering standards, documentation, tests, and CI
checks. It intentionally contains no running data pipelines and no Docker services. Kafka,
Debezium, MinIO, Airflow, Spark, Snowflake, and dbt models will be implemented in later phases.

Targets and scale figures in the documentation are design assumptions until a benchmark or an
end-to-end test records an observed result.

## Target capabilities

- Batch and streaming ingestion.
- PostgreSQL change data capture through Debezium and Kafka.
- Python ingestion and data-quality services.
- Immutable Bronze storage in MinIO and conformed Silver processing.
- Snowflake analytics warehouse with dbt staging, intermediate, and marts layers.
- SCD Type 2 dimensions and late-arriving-safe incremental facts.
- Settlement reconciliation and payment operations analytics.
- Airflow orchestration, observability, CI/CD, and business dashboards.

## Planned data flow

```text
Source systems
  |-- PostgreSQL OLTP -- Debezium CDC -- Kafka
  |-- Payment domain events ----------- Kafka
  `-- Partner settlement files -------- Batch ingestion
                         |
                         v
                 MinIO Bronze (raw)
                         |
                         v
                 Silver processing
                         |
                         v
             Snowflake + dbt data products
                         |
             +-----------+------------+
             |                        |
    Operations analytics     Finance reconciliation
```

This is a target-state view, not a claim that the components are already implemented.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/` | Python packages for shared code, generators, ingestion, processing, quality, and observability. |
| `infrastructure/` | Versioned infrastructure configuration, grouped by platform component. |
| `airflow/` | Future DAGs, shared orchestration assets, and DAG tests. |
| `dbt/` | dbt project configuration and future staging, intermediate, mart, snapshot, macro, and test assets. |
| `contracts/` | Versioned contracts for event streams and batch sources. |
| `dashboards/` | Operations, finance, and executive dashboard definitions. |
| `tests/` | Unit, integration, and end-to-end tests with explicit scope boundaries. |
| `docs/` | Business context, architecture, data models, ADRs, runbooks, and roadmap. |
| `.github/workflows/` | Automated repository quality gates. |

## Local setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Create a local environment file before a later phase needs runtime configuration:

```bash
cp .env.example .env
```

Keep real credentials only in `.env` or an external secret manager. Never commit them.

## Quality checks

Run the checks directly:

```bash
ruff check .
pytest
python -m yamllint .
docker compose --env-file .env.example config --quiet
```

On systems with GNU Make, the same checks are available through:

```bash
make validate
```

## Documentation entry points

- [Business case](docs/business/business-case.md)
- [Business and platform requirements](docs/business/requirements.md)
- [Target architecture](docs/architecture/target-architecture.md)
- [Source model](docs/data-model/source-model.md)
- [Implementation roadmap](docs/roadmap.md)

## Security baseline

- The repository contains variable names and non-sensitive local defaults only.
- Secret-bearing variables are empty in `.env.example`.
- The example dbt profile resolves all connection settings from environment variables.
- `.env`, local dbt profiles, logs, build outputs, and data files are ignored by Git.
- Least-privilege platform roles and credential rotation will be implemented before external systems
  are connected.
