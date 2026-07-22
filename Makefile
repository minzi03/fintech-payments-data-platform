PYTHON ?= python
COMPOSE_ENV ?= $(if $(wildcard .env),.env,.env.example)
GENERATOR_ARGS ?= --once --seed 42 --customers 10 --merchants 5 --transactions 50

-include $(COMPOSE_ENV)
export APP_ENV LOG_LEVEL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL
export GENERATOR_SEED GENERATOR_CUSTOMERS GENERATOR_MERCHANTS GENERATOR_TRANSACTIONS
export GENERATOR_INVALID_RATE GENERATOR_DUPLICATE_RATE

.PHONY: help install lint format format-check test test-unit test-integration coverage yaml yaml-check compose-config compose-check validate quality postgres-up postgres-down postgres-logs postgres-reset generate-data clean

help:
	@echo "Targets: install lint format-check test-unit test-integration validate postgres-up postgres-down postgres-logs postgres-reset generate-data clean"

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
	docker compose --env-file $(COMPOSE_ENV) down

postgres-logs:
	docker compose --env-file $(COMPOSE_ENV) logs --tail=200 -f postgres

postgres-reset:
	@echo "WARNING: this permanently deletes the PostgreSQL named volume and all local data."
	@test "$(CONFIRM)" = "1" || (echo "Re-run with CONFIRM=1 to continue."; exit 1)
	docker compose --env-file $(COMPOSE_ENV) down --volumes --remove-orphans
	docker compose --env-file $(COMPOSE_ENV) up -d --wait postgres

generate-data:
	$(PYTHON) -m generators.cli $(GENERATOR_ARGS)

clean:
	$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for path in (Path('tmp/pytest_cache'), Path('.ruff_cache'), Path('htmlcov'), Path('build'), Path('dist'))]; [path.unlink(missing_ok=True) for path in (Path('.coverage'), Path('coverage.xml'))]"
