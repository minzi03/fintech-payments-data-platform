PYTHON ?= python
COMPOSE_ENV ?= $(if $(wildcard .env),.env,.env.example)
GENERATOR_ARGS ?= --once --seed 42 --customers 10 --merchants 5 --transactions 50
SETTLEMENT_PARTNER ?= VCB
SETTLEMENT_DATE ?= 2026-07-22
SETTLEMENT_CONTRACT ?= contracts/batch/settlement_v1.yml
SETTLEMENT_INPUT_DIR ?= data/inbound/settlements
SETTLEMENT_FIXTURE_ARGS ?= --partner-id $(SETTLEMENT_PARTNER) --settlement-date $(SETTLEMENT_DATE) --seed $(SETTLEMENT_FIXTURE_SEED)
SETTLEMENT_INGEST_ARGS ?= --input-dir $(SETTLEMENT_INPUT_DIR) --partner-id $(SETTLEMENT_PARTNER) --contract $(SETTLEMENT_CONTRACT)

-include $(COMPOSE_ENV)
export APP_ENV LOG_LEVEL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL
export GENERATOR_SEED GENERATOR_CUSTOMERS GENERATOR_MERCHANTS GENERATOR_TRANSACTIONS
export GENERATOR_INVALID_RATE GENERATOR_DUPLICATE_RATE
export SETTLEMENT_INBOUND_DIR SETTLEMENT_BRONZE_DIR SETTLEMENT_QUARANTINE_DIR
export SETTLEMENT_MANIFEST_DB SETTLEMENT_FIXTURE_SEED
export STORAGE_BACKEND MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_SECURE MINIO_REGION
export MINIO_BRONZE_BUCKET MINIO_QUARANTINE_BUCKET MINIO_API_PORT MINIO_CONSOLE_PORT
export MINIO_CONNECT_TIMEOUT_SECONDS MINIO_READ_TIMEOUT_SECONDS MINIO_MAX_RETRIES

.PHONY: help install lint format format-check test test-unit test-integration test-batch-unit test-batch-integration test-minio-integration coverage yaml yaml-check compose-config compose-check validate quality postgres-up postgres-down postgres-logs postgres-reset minio-up minio-down minio-logs minio-reset generate-data generate-settlement-fixtures ingest-settlements ingest-settlements-minio clean-runtime-data clean

help:
	@echo "Targets: install lint format-check test-unit test-integration test-batch-unit test-batch-integration test-minio-integration validate postgres-up postgres-down postgres-reset minio-up minio-down minio-reset generate-data generate-settlement-fixtures ingest-settlements ingest-settlements-minio clean-runtime-data clean"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest -m "not integration"

test-unit:
	$(PYTHON) -m pytest tests/unit

test-integration:
	$(PYTHON) -m pytest -m integration

test-batch-unit:
	$(PYTHON) -m pytest tests/unit/ingestion/batch

test-batch-integration:
	$(PYTHON) -m pytest -m batch_integration

test-minio-integration:
	RUN_MINIO_INTEGRATION=1 $(PYTHON) -m pytest -m minio_integration

coverage:
	$(PYTHON) -m pytest --cov=src --cov-report=term-missing --cov-report=xml

yaml-check:
	$(PYTHON) -m yamllint .

yaml: yaml-check

compose-check:
	docker compose --env-file $(COMPOSE_ENV) config --quiet

compose-config: compose-check

validate: lint format-check test yaml-check compose-check

quality: validate

postgres-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --wait postgres

postgres-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force postgres

postgres-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f postgres

postgres-reset:
	@echo "WARNING: this permanently deletes the PostgreSQL named volume and all local data."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force postgres
	docker volume rm fintech-payments-postgres-data
	docker compose --env-file $(COMPOSE_ENV) up -d --wait postgres

minio-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --wait minio
	docker compose --env-file $(COMPOSE_ENV) up minio-init

minio-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force minio-init minio

minio-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f minio minio-init

minio-reset:
	@echo "WARNING: this permanently deletes the MinIO named volume and all local objects."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force minio-init minio
	docker volume rm fintech-payments-minio-data
	docker compose --env-file $(COMPOSE_ENV) up -d --wait minio
	docker compose --env-file $(COMPOSE_ENV) up minio-init

generate-data:
	$(PYTHON) -m generators.cli $(GENERATOR_ARGS)

generate-settlement-fixtures:
	$(PYTHON) -m ingestion.batch.cli generate-settlement-fixtures --output-dir $(SETTLEMENT_INPUT_DIR) $(SETTLEMENT_FIXTURE_ARGS)

ingest-settlements:
	$(PYTHON) -m ingestion.batch.cli ingest-settlements $(SETTLEMENT_INGEST_ARGS)

ingest-settlements-minio:
	$(PYTHON) -m ingestion.batch.cli ingest-settlements --storage-backend minio $(SETTLEMENT_INGEST_ARGS)

clean-runtime-data:
	@echo "WARNING: this permanently deletes generated settlement inbound, Bronze, quarantine, and control data."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	$(PYTHON) -c "from pathlib import Path; import shutil; root=Path('data').resolve(); targets=[(root/path).resolve() for path in ('inbound/settlements','bronze/settlements','quarantine/settlements','control')]; assert all(root in target.parents for target in targets); [shutil.rmtree(target, ignore_errors=True) for target in targets]"

clean:
	$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for path in (Path('tmp/pytest_cache'), Path('.ruff_cache'), Path('htmlcov'), Path('build'), Path('dist'))]; [path.unlink(missing_ok=True) for path in (Path('.coverage'), Path('coverage.xml'))]"
