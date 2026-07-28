# Portal testing

## Backend

`apps/portal-api/tests/unit` covers typed production safety, correlation validation, dependency
aggregation, timeout mapping, telemetry, and log redaction. `tests/integration` creates the real
FastAPI application and verifies routes, Problem Details, security headers, CORS, trusted hosts,
safe metadata, correlation propagation, provider refresh concurrency, rotation/reuse detection,
bounded provider-outage recovery, back-channel replay protection, logout, and crypto-erasure.

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

The disposable real-Keycloak lifecycle check additionally requires operator access to the local
Compose control plane:

```bash
PORTAL_E2E_AUTH=1 \
PORTAL_E2E_PROVIDER_LIFECYCLE=1 \
PORTAL_E2E_EXTERNAL=1 \
PORTAL_WEB_URL=http://localhost:3000 \
pnpm --filter @fintech/portal-web exec playwright test \
  tests/e2e/security/provider-session-lifecycle.spec.ts --project chromium
```

It verifies server-side refresh-token rotation, continued browser session validity, absence of
provider tokens in browser storage/cookies, provider logout and revocation, durable lifecycle audit
events, and terminal encrypted-envelope disposal.

The provider refresh coordinator load harness is:

```bash
python apps/portal-api/scripts/validate_provider_session_load.py
```

Its default run completes 10,000 refresh operations through the production lifecycle service with
bounded simulated provider latency and one recoverable provider outage per 50 operations. It fails
on duplication, incomplete work, excessive p95 latency, or excessive traced allocation.

## Container security and smoke checks

CI builds both images, asserts configured users are non-root, starts the isolated Portal services,
waits for health, and executes the smoke suite. Existing data-platform tests remain in their
current jobs and are not weakened.
