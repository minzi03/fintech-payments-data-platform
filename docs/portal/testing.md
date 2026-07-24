# Portal testing

## Backend

`apps/portal-api/tests/unit` covers typed production safety, correlation validation, dependency
aggregation, timeout mapping, telemetry, and log redaction. `tests/integration` creates the real
FastAPI application and verifies routes, Problem Details, security headers, CORS, trusted hosts,
safe metadata, and correlation propagation.

```bash
make portal-api-test
```

This runs Ruff, formatting checks, strict mypy, unit tests, and integration tests.

## Frontend

Vitest and Testing Library cover the application shell, loading/ready/degraded/not-ready/network
states, generated-contract responses, Problem Details, retry policy, development route policy,
keyboard navigation, semantic landmarks, and axe accessibility.

```bash
make portal-web-test
```

The test environment does not persist query data, credentials, or tokens.

## Contract drift

```bash
make portal-contract-check
```

CI regenerates OpenAPI and the TypeScript client and then checks the two generated directories for
changes. A backend response-model change without regenerated artifacts fails the job.

## End-to-end

```bash
make portal-e2e
```

Playwright starts the BFF and Portal Web, verifies landing/status connectivity, validates a
correlation-aware 404, and fails if browser traffic targets PostgreSQL, Kafka, Kafka Connect,
MinIO, or Airflow ports.

## Container security and smoke checks

CI builds both images, asserts configured users are non-root, starts the isolated Portal services,
waits for health, and executes the smoke suite. Existing data-platform tests remain in their
current jobs and are not weakened.
