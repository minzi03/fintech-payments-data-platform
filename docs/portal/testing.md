# Portal testing

Security scanner orchestration is documented in
[`security-scanning.md`](security-scanning.md). Use `make security-policy` for the non-network policy
and identity check, `make security-fast` for the pull-request-equivalent source/dependency/secret
gate, and `make security-images` only after the reproducible Portal artifact manifest and exact
images exist. These commands scan Git-index inputs and do not inspect unrestricted local untracked
content.

## Backend

`apps/portal-api/tests/unit` covers typed production safety, correlation validation, dependency
aggregation, timeout mapping, telemetry, log redaction, and the disposable-database refusal
boundary. `tests/integration` creates the real FastAPI application and verifies routes, Problem
Details, security headers, CORS, trusted hosts, safe metadata, correlation propagation, provider
refresh concurrency, rotation/reuse detection, bounded provider-outage recovery, back-channel
replay protection, logout, and crypto-erasure.

```bash
make portal-api-test
```

The ordinary backend target deliberately excludes tests marked `destructive_migration` and the
real-Redis file that has its own explicit dependency workflow. It cannot run Alembic downgrade.
Database-backed integration and migration validation are separate explicit targets:

```bash
make portal-api-integration-test
make portal-migration-test
```

Both commands create their own isolated Compose project and a unique PostgreSQL database. The
orchestrator creates run-scoped credentials, installs a database marker outside the
Alembic-managed schemas, and supplies a short-lived one-use destructive token. The test process
must prove:

- exact database, run, Compose-project, and role identities;
- a current unconsumed marker and matching token;
- loopback-only connectivity and a non-persistent database name;
- zero pre-existing Portal authority state.

Any failed proof stops before migration or cleanup. Arbitrary URLs, normal `portal_control`,
payments, Airflow, PostgreSQL maintenance databases, remote hosts, missing role URLs, and fallback
from `TEST_DATABASE_URL` to `DATABASE_URL` are refused. The raw token and credential-bearing URLs
are never logged. The disposable marker survives `alembic downgrade base`; the orchestrator
verifies token consumption and tears down only its uniquely named Compose project.

Container hardening has separate static and runtime validation:

```bash
python scripts/portal/verify_container_hardening.py
docker compose --env-file .env.example up -d --no-build --wait \
  portal-api portal-audit-worker portal-redis portal-web
python scripts/portal/verify_container_hardening.py --runtime
```

The runtime verifier reports only control identifiers and service names. It does not print
environment values. See [Portal container hardening](container-hardening.md) for the control and
exception model.

The ordinary target runs Ruff, formatting checks, strict mypy, non-database tests, and integration
cases that do not need PostgreSQL or Redis. CI executes database and real-Redis checks through
explicit dependency steps, so destructive migration validation is never reachable through
ordinary backend validation. Redis is not started by the disposable PostgreSQL orchestrator.

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

The real-Redis abuse suite and distributed load harness are:

```bash
docker compose up -d portal-redis
cd apps/portal-api
python -m pytest tests/integration/test_redis_abuse_enforcement.py
python scripts/validate_abuse_protection_load.py
```

The tests use two independent Redis clients as application replicas, assert one atomic shared
quota, validate owner-safe provider leases, force `NOSCRIPT`, and wait for TTL cleanup. The default
load run performs 10,000 multi-dimensional evaluations with 100 workers and fails on over-quota,
provider concurrency overflow, or leaked keys. Redis restart and combined provider/Redis outage
procedures are documented in [Distributed abuse protection](abuse-protection.md).

The PostgreSQL audit outbox suite and distributed delivery load harness are:

```bash
cd apps/portal-api
python -m pytest tests/integration/test_audit_outbox_worker.py
python scripts/validate_audit_outbox_load.py
```

The integration suite covers transactional enqueue, privacy-safe versioned payloads, local-only
recursion prevention, `SKIP LOCKED` claims, lease recovery and stale fencing, idempotent delivery,
dead-letter/requeue, retention boundaries, and distributed maintenance locking. The default load
run commits 10,000 events and drains them with eight competing workers; it fails on duplicate
receipts, incomplete work, stale finalization, latency-budget violations, or excessive traced
allocation. Operational outage and recovery procedures are documented in
[Audit outbox delivery and background maintenance](audit-outbox-and-maintenance.md).

## Container security and smoke checks

CI builds both images, asserts configured users are non-root, starts the isolated Portal services,
waits for health, and executes the smoke suite. Existing data-platform tests remain in their
current jobs and are not weakened.
