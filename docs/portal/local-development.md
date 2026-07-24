# Portal local development

## Prerequisites

- Python 3.11
- Node.js 22.18 or newer (the container pins Node 22.20.0)
- pnpm 11.9.0
- Docker Compose for container startup

## Setup

```bash
cp .env.example .env
make portal-install
make portal-contracts
```

The Python API can run independently:

```bash
PYTHONPATH=apps/portal-api/app \
python -m uvicorn portal_api.main:app --host 127.0.0.1 --port 8010 --reload
```

In a second terminal:

```bash
PORTAL_API_INTERNAL_URL=http://127.0.0.1:8010 \
pnpm --filter @fintech/portal-web dev
```

Or start both isolated containers without Kafka, Airflow, MinIO, or PostgreSQL:

```bash
make portal-up
```

## URLs

- Portal Web: <http://localhost:3000>
- System Status: <http://localhost:3000/system-status>
- Portal API liveness: <http://localhost:8010/health/live>
- Portal API readiness: <http://localhost:8010/health/ready>
- Development OpenAPI: <http://localhost:8010/docs>

## Quality commands

```bash
make portal-config-check
make portal-contract-check
make portal-test
make portal-build
make portal-e2e
```

Install the Playwright browser once when required:

```bash
pnpm --filter @fintech/portal-web exec playwright install chromium
```

## Shutdown

```bash
make portal-down
```

Portal services use no persistent volumes or Portal database. Removing them does not alter
data-platform state.
