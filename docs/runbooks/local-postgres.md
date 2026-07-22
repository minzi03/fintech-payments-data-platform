# Local PostgreSQL Runbook

## Scope

This runbook operates the Phase 1 single-node local PostgreSQL source. It is not a production backup,
recovery, high-availability, or security procedure.

## Prerequisites and configuration

Install Docker Compose and Python 3.11+. From the repository root:

```bash
cp .env.example .env
python -m pip install -e ".[dev]"
```

PowerShell uses `Copy-Item .env.example .env`. Update only the ignored `.env` file. Required variables
are `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`DATABASE_URL`. Do not paste the URL into logs or tickets because it contains a password.

## Validate and start

```bash
docker compose config --quiet
docker compose up -d --wait postgres
docker compose ps postgres
```

Equivalent Make target:

```bash
make postgres-up
```

The container is ready only when the health status is `healthy`. The health check uses `pg_isready`
inside the container. Initialization logs are available with:

```bash
docker compose logs postgres
# or
make postgres-logs
```

## Initialization lifecycle

On an empty named volume, PostgreSQL executes the read-only scripts in lexical order from
`infrastructure/postgres/init/`. On subsequent starts, PostgreSQL reuses the existing database and
does not rerun the initialization directory. Editing an init script therefore does not migrate an
existing local volume.

## Generate a dataset

```bash
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250 --invalid-rate 0.02 --duplicate-rate 0.02"
```

The Makefile loads `.env` when present. Without GNU Make, export the six PostgreSQL variables or
`DATABASE_URL` into the current shell before running `python -m generators.cli` with the same
arguments; the Python process deliberately does not parse secret files itself.

The run is atomic. If persistence fails, the outer database transaction is rolled back. Invalid and
duplicate rates control explicit savepoint-backed constraint probes; rejected probes are expected and
do not remain in the database.

The same seed and generator configuration produce the same in-memory identifiers, amounts, statuses,
and timestamps. Persisting the same seed twice is expected to hit uniqueness constraints; use a new
seed for another committed dataset or reset the local database.

## Test commands

Unit tests do not require Docker:

```bash
pytest -m "not integration"
# or
make test-unit
```

With PostgreSQL healthy:

```bash
export TEST_DATABASE_URL="$DATABASE_URL"
pytest -m integration
# or
make test-integration
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = $env:DATABASE_URL
pytest -m integration
```

Integration tests open a transaction per test and roll it back, leaving committed local generator
data unchanged.

## Stop and restart

```bash
make postgres-down       # preserves the named volume
make postgres-up
```

`docker compose down` removes the container/network but preserves Phase 1 data in the named volume.

## Destructive reset

**Warning: reset deletes the named PostgreSQL volume and all local database data. It cannot be
recovered unless separately backed up.** Verify that the current directory is this repository and
that no data must be retained, then run:

```bash
make postgres-reset CONFIRM=1
```

The target stops Compose, removes its volumes, then recreates PostgreSQL so the ordered init scripts
run against a clean database. Never use this procedure for a shared or production database.

## Troubleshooting

- **Docker daemon unavailable:** start Docker Desktop/Engine, then retry `docker info`.
- **Port already in use:** choose another local `POSTGRES_PORT` in `.env`; keep the container port at
  5432 through Compose mapping.
- **Container unhealthy:** inspect `docker compose logs postgres`; confirm database, user, and password
  variables are nonempty and consistent with `DATABASE_URL`.
- **Schema change not visible:** init scripts do not rerun on a populated volume. Use a migration or,
  for disposable local data only, the destructive reset.
- **Authentication failure:** ensure the shell/test URL matches `.env`; never print the full URL.

## Phase 1 limitations

- One local PostgreSQL node, one application role, no TLS, no HA, no automated backup, and no load SLA.
- No ledger posting, account balance mutation, cross-currency conversion, or settlement records.
- No Kafka, Debezium, MinIO, Airflow, Spark, dbt execution, Snowflake, BI, or observability services.
