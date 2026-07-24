PYTHON ?= python
PNPM ?= pnpm
COMPOSE_ENV ?= $(if $(wildcard .env),.env,.env.example)
GENERATOR_ARGS ?= --once --seed 42 --customers 10 --merchants 5 --transactions 50
SETTLEMENT_PARTNER ?= VCB
SETTLEMENT_DATE ?= 2026-07-22
SETTLEMENT_CONTRACT ?= contracts/batch/settlement_v1.yml
SETTLEMENT_INPUT_DIR ?= data/inbound/settlements
SETTLEMENT_FIXTURE_ARGS ?= --partner-id $(SETTLEMENT_PARTNER) --settlement-date $(SETTLEMENT_DATE) --seed $(SETTLEMENT_FIXTURE_SEED)
SETTLEMENT_INGEST_ARGS ?= --input-dir $(SETTLEMENT_INPUT_DIR) --partner-id $(SETTLEMENT_PARTNER) --contract $(SETTLEMENT_CONTRACT)
CDC_TABLE ?= payment_transactions
CDC_CONSUMER_ARGS ?= --storage-backend minio
CDC_CONSUMER_INSPECT_ARGS ?=
SILVER_CDC_ARGS ?= --storage-backend minio --input-prefix cdc/
SILVER_SETTLEMENT_ARGS ?= --storage-backend minio --input-prefix settlements/
SILVER_INSPECT_ARGS ?= --storage-backend minio
AIRFLOW_DAG_ID ?= settlement_batch_pipeline
AIRFLOW_LOGICAL_DATE ?= 2026-07-23T00:00:00+00:00
BACKFILL_CONF ?=

-include $(COMPOSE_ENV)
export APP_ENV LOG_LEVEL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL
export GENERATOR_SEED GENERATOR_CUSTOMERS GENERATOR_MERCHANTS GENERATOR_TRANSACTIONS
export GENERATOR_INVALID_RATE GENERATOR_DUPLICATE_RATE
export SETTLEMENT_INBOUND_DIR SETTLEMENT_BRONZE_DIR SETTLEMENT_QUARANTINE_DIR
export SETTLEMENT_MANIFEST_DB SETTLEMENT_FIXTURE_SEED
export STORAGE_BACKEND MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_SECURE MINIO_REGION
export MINIO_BRONZE_BUCKET MINIO_QUARANTINE_BUCKET MINIO_API_PORT MINIO_CONSOLE_PORT
export MINIO_SILVER_BUCKET
export MINIO_CONNECT_TIMEOUT_SECONDS MINIO_READ_TIMEOUT_SECONDS MINIO_MAX_RETRIES
export POSTGRES_MAX_REPLICATION_SLOTS POSTGRES_MAX_WAL_SENDERS
export KAFKA_CLUSTER_ID KAFKA_BOOTSTRAP_SERVERS KAFKA_EXTERNAL_PORT KAFKA_CONNECT_PORT
export KAFKA_CONNECT_URL KAFKA_TOPIC_PREFIX KAFKA_DEFAULT_PARTITIONS KAFKA_RETENTION_MS
export DEBEZIUM_CONNECTOR_NAME DEBEZIUM_SLOT_NAME DEBEZIUM_PUBLICATION_NAME
export DEBEZIUM_DATABASE_HOST DEBEZIUM_DATABASE_PORT DEBEZIUM_DATABASE_NAME
export DEBEZIUM_DATABASE_USER DEBEZIUM_DATABASE_PASSWORD DEBEZIUM_HEARTBEAT_INTERVAL_MS
export DEBEZIUM_SNAPSHOT_MODE CDC_HTTP_TIMEOUT_SECONDS CDC_HTTP_MAX_ATTEMPTS
export CDC_CONSUMER_GROUP_ID CDC_CONSUMER_CLIENT_ID CDC_CONSUMER_TOPICS
export CDC_CONSUMER_AUTO_OFFSET_RESET CDC_CONSUMER_BATCH_SIZE
export CDC_CONSUMER_FLUSH_INTERVAL_SECONDS CDC_CONSUMER_POLL_TIMEOUT_MS
export CDC_CONSUMER_MAX_POLL_INTERVAL_MS CDC_CONSUMER_SESSION_TIMEOUT_MS
export CDC_CONSUMER_HEARTBEAT_INTERVAL_MS CDC_CONSUMER_MAX_RETRIES
export CDC_CONSUMER_RETRY_BACKOFF_SECONDS CDC_DLQ_TOPIC CDC_BRONZE_BUCKET
export CDC_QUARANTINE_BUCKET CDC_SCHEMA_VERSION CDC_CONSUMER_MANIFEST_DB
export CDC_CONSUMER_TEMP_DIR CDC_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS
export SILVER_LOCAL_ROOT SILVER_MANIFEST_DB SILVER_TEMP_DIR SILVER_CODE_VERSION
export SILVER_SCHEMA_VERSION SILVER_SUPPORTED_CDC_SCHEMA SILVER_SETTLEMENT_CONTRACT
export SILVER_MAX_OBJECTS
export AIRFLOW_IMAGE_NAME AIRFLOW_UID AIRFLOW_WEB_PORT AIRFLOW_EXECUTOR
export AIRFLOW_DATABASE_USER AIRFLOW_DATABASE_PASSWORD AIRFLOW_DATABASE_NAME
export CONTROL_DATABASE_USER CONTROL_DATABASE_PASSWORD AIRFLOW_FERNET_KEY AIRFLOW_SECRET_KEY
export AIRFLOW_ADMIN_USER AIRFLOW_TIMEZONE AIRFLOW_SETTLEMENT_SCHEDULE
export AIRFLOW_CDC_HEALTH_SCHEDULE AIRFLOW_SILVER_SCHEDULE AIRFLOW_TASK_RETRIES
export AIRFLOW_RETRY_DELAY_SECONDS AIRFLOW_TASK_TIMEOUT_SECONDS SETTLEMENT_PARTNER_ID
export SETTLEMENT_REJECTION_WARN_RATE SETTLEMENT_REJECTION_FAIL_RATE
export SILVER_REJECTION_WARN_RATE SILVER_REJECTION_FAIL_RATE
export CDC_LAG_WARN_THRESHOLD CDC_LAG_FAIL_THRESHOLD
export CDC_FRESHNESS_WARN_SECONDS CDC_FRESHNESS_FAIL_SECONDS

.PHONY: help install lint format format-check test test-unit test-integration test-batch-unit test-batch-integration test-minio-integration test-cdc-integration test-cdc-consumer-unit test-cdc-consumer-integration test-silver-unit test-silver-integration test-airflow-unit test-airflow-integration coverage yaml yaml-check compose-config compose-check validate quality portal-install portal-openapi portal-client portal-contracts portal-contract-check portal-api-test portal-web-test portal-test portal-build portal-config-check portal-up portal-down portal-logs portal-e2e postgres-up postgres-down postgres-logs postgres-reset minio-up minio-down minio-logs minio-reset kafka-up kafka-down kafka-logs connect-logs cdc-up cdc-down cdc-status cdc-register cdc-restart cdc-delete cdc-inspect cdc-consumer-run cdc-consumer-once cdc-consumer-logs inspect-cdc-bronze reset-cdc-consumer-state silver-process-cdc silver-process-settlements silver-process-once silver-inspect reset-silver-state airflow-build airflow-init airflow-up airflow-down airflow-logs airflow-shell airflow-demo-login-info airflow-show-demo-password airflow-dags-list airflow-dag-test trigger-settlement-pipeline trigger-cdc-silver-pipeline trigger-backfill reset-airflow-metadata generate-data generate-settlement-fixtures ingest-settlements ingest-settlements-minio clean-runtime-data clean

help:
	@echo "Targets: install lint format-check test-unit test-integration portal-install portal-contracts portal-test portal-build portal-up portal-e2e portal-down postgres-up minio-up kafka-up cdc-up airflow-build airflow-init airflow-up airflow-down"

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

test-cdc-integration:
	RUN_CDC_INTEGRATION=1 $(PYTHON) -m pytest -m cdc_integration

test-cdc-consumer-unit:
	$(PYTHON) -m pytest tests/unit/ingestion/cdc_consumer

test-cdc-consumer-integration:
	RUN_CDC_CONSUMER_INTEGRATION=1 $(PYTHON) -m pytest -m cdc_consumer_integration

test-silver-unit:
	$(PYTHON) -m pytest tests/unit/processing/silver

test-silver-integration:
	RUN_SILVER_INTEGRATION=1 $(PYTHON) -m pytest -m silver_integration

test-airflow-unit:
	$(PYTHON) -m pytest airflow/tests tests/unit/orchestration

test-airflow-integration:
	RUN_AIRFLOW_INTEGRATION=1 $(PYTHON) -m pytest -m airflow_integration

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

portal-install:
	$(PYTHON) -m pip install -e "./apps/portal-api[dev]"
	$(PNPM) install --frozen-lockfile

portal-openapi:
	PYTHONPATH=apps/portal-api/app $(PYTHON) apps/portal-api/scripts/generate_openapi.py --output packages/portal-contracts/openapi/portal-api-v1.json

portal-client:
	$(PNPM) --filter @fintech/portal-contracts generate

portal-contracts: portal-openapi portal-client

portal-contract-check: portal-contracts
	git diff --exit-code -- packages/portal-contracts/openapi packages/portal-contracts/src/generated

portal-api-test:
	$(PYTHON) -m ruff check apps/portal-api
	$(PYTHON) -m ruff format --check apps/portal-api
	$(PYTHON) -m mypy --config-file apps/portal-api/pyproject.toml
	$(PYTHON) -m pytest -c apps/portal-api/pyproject.toml apps/portal-api/tests

portal-web-test:
	$(PNPM) --filter @fintech/portal-web format:check
	$(PNPM) --filter @fintech/portal-web lint
	$(PNPM) --filter @fintech/portal-web typecheck
	$(PNPM) --filter @fintech/portal-web test

portal-test: portal-api-test portal-web-test

portal-build:
	$(PNPM) --filter @fintech/portal-web build
	docker compose --env-file $(COMPOSE_ENV) build portal-api portal-web

portal-config-check:
	PYTHONPATH=apps/portal-api/app $(PYTHON) apps/portal-api/scripts/validate_config.py

portal-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --build --wait portal-api portal-web

portal-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force portal-web portal-api

portal-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f portal-web portal-api

portal-e2e:
	$(PNPM) --filter @fintech/portal-web e2e

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

kafka-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --wait kafka

kafka-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force connector-init kafka-connect kafka

kafka-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f kafka

connect-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f kafka-connect connector-init

cdc-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --wait postgres kafka kafka-connect
	docker compose --env-file $(COMPOSE_ENV) up --build connector-init

cdc-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force connector-init kafka-connect kafka

cdc-status:
	$(PYTHON) scripts/cdc/connector_status.py

cdc-register:
	$(PYTHON) scripts/cdc/register_connector.py

cdc-restart:
	$(PYTHON) scripts/cdc/connector_status.py --restart --wait-running

cdc-delete:
	@echo "WARNING: deleting the connector stops CDC; the replication slot is retained."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	$(PYTHON) scripts/cdc/delete_connector.py --confirm

cdc-inspect:
	$(PYTHON) scripts/cdc/inspect_topic.py --table $(CDC_TABLE)

cdc-consumer-run:
	$(PYTHON) -m ingestion.cdc_consumer.cli run $(CDC_CONSUMER_ARGS)

cdc-consumer-once:
	$(PYTHON) -m ingestion.cdc_consumer.cli run --once $(CDC_CONSUMER_ARGS)

cdc-consumer-logs:
	docker compose --env-file $(COMPOSE_ENV) --profile cdc-consumer logs --tail=200 -f cdc-consumer

inspect-cdc-bronze:
	$(PYTHON) -m ingestion.cdc_consumer.cli inspect --storage-backend minio $(CDC_CONSUMER_INSPECT_ARGS)

reset-cdc-consumer-state:
	@echo "WARNING: this deletes only the CDC consumer manifest/temp volumes and offsets for $(CDC_CONSUMER_GROUP_ID)."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) --profile cdc-consumer rm --stop --force cdc-consumer
	docker compose --env-file $(COMPOSE_ENV) exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --delete --group $(CDC_CONSUMER_GROUP_ID)
	docker volume rm fintech-payments-cdc-consumer-state fintech-payments-cdc-consumer-tmp

silver-process-cdc:
	$(PYTHON) -m processing.silver.cli process-cdc $(SILVER_CDC_ARGS)

silver-process-settlements:
	$(PYTHON) -m processing.silver.cli process-settlements $(SILVER_SETTLEMENT_ARGS)

silver-process-once:
	$(PYTHON) -m processing.silver.cli process-cdc --max-objects 1 $(SILVER_CDC_ARGS)

silver-inspect:
	$(PYTHON) -m processing.silver.cli inspect $(SILVER_INSPECT_ARGS)

reset-silver-state:
	@echo "WARNING: this deletes only the local Silver processing SQLite manifest; Bronze and object data remain untouched."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	$(PYTHON) -m processing.silver.cli reset-state --confirm

airflow-build:
	docker compose --env-file $(COMPOSE_ENV) build airflow-init airflow-webserver airflow-scheduler airflow-dag-processor

airflow-init:
	docker compose --env-file $(COMPOSE_ENV) up --build airflow-init

airflow-up:
	docker compose --env-file $(COMPOSE_ENV) up -d --build --wait airflow-webserver airflow-scheduler airflow-dag-processor

airflow-down:
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force airflow-webserver airflow-scheduler airflow-dag-processor airflow-init airflow-postgres

airflow-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f airflow-webserver airflow-scheduler airflow-dag-processor

airflow-shell:
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler bash

airflow-demo-login-info:
	@echo "Airflow URL: http://localhost:$(AIRFLOW_WEB_PORT)"
	@echo "Username: $(AIRFLOW_ADMIN_USER)"
	@echo "Password retrieval: run 'make airflow-show-demo-password CONFIRM=1' privately."
	@echo "WARNING: Never run the password target while sharing or recording the screen."

airflow-show-demo-password:
	@test "$(CONFIRM)" = "1" || (echo "Refusing to display password. Re-run with CONFIRM=1."; exit 1)
	@docker compose --env-file $(COMPOSE_ENV) exec -T airflow-webserver python -c 'import json, os, sys; from pathlib import Path; configured_path=os.environ.get("AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE", ""); configured_path or sys.exit("Airflow password file is not configured"); path=Path(configured_path); path.is_file() or sys.exit("Airflow password file does not exist; start the API server first"); user_spec=os.environ.get("AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS", ""); username=user_spec.split(",", 1)[0].split(":", 1)[0].strip(); username or sys.exit("Airflow demo username is not configured"); passwords=json.loads(path.read_text(encoding="utf-8")); password=passwords.get(username); isinstance(password, str) and bool(password) or sys.exit("Airflow password is missing for the configured demo user"); print(password)'

airflow-dags-list:
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler airflow dags list

airflow-dag-test:
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler airflow dags test $(AIRFLOW_DAG_ID) $(AIRFLOW_LOGICAL_DATE)

trigger-settlement-pipeline:
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler airflow dags trigger settlement_batch_pipeline

trigger-cdc-silver-pipeline:
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler airflow dags trigger cdc_silver_processing_pipeline

trigger-backfill:
	@test -n "$(BACKFILL_CONF)" || (echo "BACKFILL_CONF is required; provide a unique request_id."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) exec airflow-scheduler airflow dags trigger data_platform_backfill --conf '$(BACKFILL_CONF)'

reset-airflow-metadata:
	@echo "WARNING: this deletes only Airflow metadata, control-plane rows, Airflow logs/config, and Airflow component SQLite manifests. OLTP, Kafka, Bronze, and Silver objects remain untouched."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) rm --stop --force airflow-webserver airflow-scheduler airflow-dag-processor airflow-init airflow-postgres
	docker volume rm fintech-payments-airflow-postgres-data fintech-payments-airflow-logs fintech-payments-airflow-component-state fintech-payments-airflow-tmp

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
