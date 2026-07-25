# Portal troubleshooting

## Portal API unavailable

Check `http://localhost:8010/health/live` and `docker compose logs portal-api`. Configuration errors
fail startup intentionally. Do not print the complete environment. Use the correlation ID from the
UI or response header to locate the matching structured request log.

## Readiness is degraded

`DEGRADED` means an optional registered adapter is not healthy; the BFF can still serve enabled
foundation capabilities. Open System Status for the safe reason and runbook link. `NOT_READY`
returns HTTP 503 only when a required enabled adapter fails.

An empty dependency list is expected in PR-PORTAL-001 and does not imply hidden connectivity.

## Frontend cannot reach the BFF

For local processes set `PORTAL_API_INTERNAL_URL=http://127.0.0.1:8010`. In Compose it is
`http://portal-api:8010`. Browser requests must remain relative to `/portal-api`; do not place a
Docker hostname or infrastructure address in public frontend variables.

## Stale generated client

Run:

```bash
make portal-contracts
make portal-contract-check
```

Review the OpenAPI and generated TypeScript diff together. Never edit `src/generated` manually.

## CORS or trusted-host rejection

Add only the exact Portal Web origin to `PORTAL_API_ALLOWED_ORIGINS` and explicit BFF hostnames to
`PORTAL_API_TRUSTED_HOSTS`. Wildcard origins and trusted hosts are rejected in production.

## Docker health check fails

Use `docker compose ps portal-api portal-web`, then inspect only those service logs. The Portal has
no dependency on Kafka, Airflow, MinIO, or PostgreSQL, so starting those services is not a remedy
for a foundation health failure.

## Correlation IDs

Provide the value shown in the UI, Problem Details body, or `X-Correlation-ID` response header.
Valid inbound IDs are preserved; unsafe IDs are replaced. A correlation ID is diagnostic metadata,
not authorization or an infrastructure identifier.
