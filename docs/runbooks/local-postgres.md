# Local PostgreSQL Runbook

## Scope

This runbook operates the Phase 1 single-node local PostgreSQL source and its Phase 4 logical
replication prerequisites. It is not a production backup, recovery, high-availability, or security
procedure.

## Prerequisites and configuration

Install Docker Compose and Python 3.11+. From the repository root:

```bash
cp .env.example .env
python -m pip install -e ".[dev]"
```

PowerShell uses `Copy-Item .env.example .env`. Update only the ignored `.env` file. Required variables
are `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`DATABASE_URL`. Phase 4 additionally uses `POSTGRES_MAX_REPLICATION_SLOTS`,
`POSTGRES_MAX_WAL_SENDERS`, and the `DEBEZIUM_DATABASE_*` variables. Do not paste passwords or the
database URL into logs or tickets.

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

Compose starts PostgreSQL with `wal_level=logical` and bounded slot/sender counts. Verify without
printing credentials:

```bash
docker compose --env-file .env exec postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SHOW wal_level; SHOW max_replication_slots; SHOW max_wal_senders;"
```

## Initialization lifecycle

On an empty named volume, PostgreSQL executes the read-only scripts in lexical order from
`infrastructure/postgres/init/`. On subsequent starts, PostgreSQL reuses the existing database and
does not rerun the initialization directory. Editing an init script therefore does not migrate an
existing local volume.

The CDC role and publication intentionally are not init SQL. The one-shot `connector-init` service
reconciles them after every CDC startup, so Phase 4 works with a populated Phase 1 volume without a
reset. It creates/rotates a dedicated LOGIN REPLICATION role, verifies `NOSUPERUSER`, grants CONNECT,
payments schema USAGE, SELECT on exactly six captured tables, and sets an explicit publication.
Changing the application schema/constraints is outside this bootstrap.

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

CDC integration commits uniquely keyed probe rows because only committed WAL changes are emitted.
Run it separately after the full CDC stack is healthy:

```bash
RUN_CDC_INTEGRATION=1 pytest -m cdc_integration
```

## Stop and restart

```bash
make postgres-down       # preserves the named volume
make postgres-up
```

`docker compose down` removes the container/network but preserves Phase 1 data in the named volume.
If a logical slot is active, stop Kafka Connect before resetting PostgreSQL.

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

## PostgreSQL limitations through Phase 4

- One local PostgreSQL node, plaintext local network, no HA, automated backup, or load SLA.
- The application bootstrap account remains local-development administrative identity. Debezium uses
  a separate non-superuser role, but production authentication/rotation and pg_hba hardening are not
  implemented.
- Replication slots can retain WAL when Connect is unavailable; inspect/remove abandoned slots only
  through the documented CDC troubleshooting procedure.
- No ledger posting, account balance mutation, cross-currency conversion, or settlement records.
- Kafka/Debezium transport is implemented, but no CDC consumer, Silver processing, Airflow, Spark,
  dbt execution, Snowflake, BI, or observability service exists.
