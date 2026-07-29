# Portal troubleshooting

## Scope

This guide covers diagnosis of the implemented local/reference Portal runtime. It is not a
production deployment manual. Start with safe health/configuration evidence and preserve
PostgreSQL authority, intentional dead-letter evidence, and disposable-test boundaries.

See [architecture boundaries](architecture-boundaries.md), [testing](testing.md), and
[audit/outbox operations](audit-outbox-and-maintenance.md) for deeper contracts.

## Portal API unavailable

Check:

```text
http://localhost:8010/health/live
http://localhost:8010/health/ready
```

Then inspect the bounded service logs:

```bash
docker compose --env-file .env.example ps portal-api
docker compose --env-file .env.example logs --tail 200 portal-api
```

Configuration, secret-reference, schema-compatibility, and required-adapter failures stop startup
intentionally. Do not print the complete environment or credential-bearing URLs. Use the
correlation ID from the UI, Problem Details response, or `X-Correlation-ID` header.

## API readiness states

When the security runtime is enabled:

- PostgreSQL is a required dependency and validates connectivity, schema compatibility, migration
  state, and bounded runtime privileges;
- Keycloak/OIDC is a required dependency and validates discovery, issuer, JWKS, timeout, and cache
  behavior;
- Redis is an optional readiness dependency for distributed abuse enforcement and has
  operation-specific fallback behavior.

An empty readiness registry is invalid and returns `NOT_READY`; it is not an expected healthy
foundation state.

| State       | Meaning                                                                | First checks                                                    |
| ----------- | ---------------------------------------------------------------------- | --------------------------------------------------------------- |
| `READY`     | Every required and registered optional dependency is `UP`              | No action                                                       |
| `DEGRADED`  | Required dependencies are `UP`; an optional dependency needs attention | Inspect Redis/optional dependency reason and fallback telemetry |
| `NOT_READY` | A required dependency is unavailable, timed out, missing, or invalid   | Inspect PostgreSQL and OIDC evidence before retrying            |

Do not mark an absent adapter healthy and do not bypass required dependencies merely to clear a
probe.

## PostgreSQL readiness failure

Confirm `portal-postgres` health before the API:

```bash
docker compose --env-file .env.example ps portal-postgres portal-migrate portal-api
```

Typical safe failure classes are connectivity, timeout, incompatible `portal_control` schema,
migration mismatch, or missing least-privilege grants. Do not point the runtime at a test database,
run a downgrade, recreate the volume, or grant superuser privileges as troubleshooting shortcuts.

The current Alembic head is `008_audit_outbox_and_maintenance`. Migration ownership belongs to
`portal-migrate`; normal API and audit-worker roles do not own schema changes.

## Keycloak/OIDC readiness failure

Check `portal-keycloak` health and the safe readiness reason. Failures may indicate:

- discovery endpoint unavailable or timed out;
- configured issuer mismatch;
- JWKS unavailable/empty;
- stale cache beyond the allowed ceiling;
- provider restart or signing-key rotation still recovering.

Do not disable issuer, signature, audience, nonce, or time validation. The provider lifecycle can
force one JWKS refresh for an unknown key; repeated failure remains fail-closed.

## Redis degradation

Redis stores reconstructible abuse-enforcement counters, penalties, and fast replay hints. It is
not session, token, logout, or durable replay authority.

When Redis is unavailable:

- API readiness can be `DEGRADED` while PostgreSQL and OIDC remain available;
- login/refresh paths use their bounded operation-specific fallback;
- local logout and crypto-erasure must remain possible;
- signed back-channel logout still uses durable PostgreSQL replay authority.

Inspect `portal-redis`, bounded abuse telemetry, and fallback activation logs. Do not weaken quotas,
move session authority to Redis, or flush Redis as a default diagnostic action.

## Audit worker health

Use the approved read-only health path:

```bash
docker compose --env-file .env.example exec -T portal-audit-worker \
  python -m portal_api.audit.worker --healthcheck
```

Worker health rules:

- `UP`: database, destination, and outbox are available with no overdue/bounded degraded evidence;
- `DEGRADED`: operationally available, but a dead letter, overdue maintenance job, or excessive
  pending age requires attention;
- `DOWN`: the worker cannot meet its operational availability contract because the outbox or
  destination is unavailable.

`DEGRADED` is operationally available when the database, destination, and outbox dependencies are
`UP` but intentional or bounded degraded evidence exists. `DOWN` means the service cannot meet its
defined operational availability contract.

The canonical validation checkpoint is:

```text
status:               DEGRADED
database:             UP
destination:          UP
outbox:               UP
pending:              0
dead_lettered:        1
maintenance_overdue:  0
```

One intentional poison event is preserved to demonstrate dead-letter handling. Do not repair,
delete, or replay it merely to force a healthy status during validation or demo preparation.

## Audit outbox or maintenance diagnosis

Authentication/security actions commit independently of archive delivery. Worker failure must not
roll back an already committed security action.

Use the worker health result and metadata-only logs to distinguish:

- pending work inside bounded retry/backoff;
- a poison event moved to dead letter;
- destination unavailability;
- stale lease recovered through fencing;
- overdue maintenance;
- explicit operator requeue.

Do not update outbox rows manually, delete the audit ledger, or run cleanup against active session
authority. Requeue is explicit and separately auditable.

## Migration validation safety

Destructive migration validation runs only through the dedicated disposable database workflow.
Ordinary backend tests exclude destructive migration tests.

```bash
python scripts/portal/run_disposable_portal_tests.py --suite migration
```

The orchestrator creates a unique local database and Compose project, installs ownership evidence
outside the Alembic-managed schema, grants a short-lived one-use destructive token, validates exact
bounded roles, and tears down only run-owned resources.

Never:

- pass `DATABASE_URL` as a fallback;
- target `portal_control` or another persistent database;
- use a remote host, superuser, `CREATEDB`, `CREATEROLE`, or replication role;
- bypass marker, run-ID, token-expiry, or token-consumption checks;
- invoke migration downgrade from an ordinary backend test command.

Any ownership-proof failure must stop before migration or cleanup.

## Frontend cannot reach the API

For local processes set `PORTAL_API_INTERNAL_URL=http://127.0.0.1:8010`. In Compose it is
`http://portal-api:8010`. Browser requests remain relative to `/portal-api`; do not place Docker
hostnames, infrastructure addresses, credentials, or server-only variables in public frontend
configuration.

Validate that Portal Web public environment/origin agrees with API CORS and OIDC redirect
authority. See [production configuration model](production-configuration-model.md).

## CORS or trusted-host rejection

Add only the exact Portal Web origin to `PORTAL_API_ALLOWED_ORIGINS` and explicit BFF hostnames to
`PORTAL_API_TRUSTED_HOSTS`. Wildcard origins/trusted hosts and insecure production combinations
are rejected.

Do not remove the staging/production security authorization guards. A syntactically valid
production profile is not production security approval.

## Stale generated client

Run:

```bash
make portal-contracts
make portal-contract-check
```

Review OpenAPI and generated TypeScript changes together. Never edit generated contract output
manually. The current OpenAPI SHA-256 is
`9fab4ac5b9e41dab87f648d2e797a99caefe51fefacacf4407df97f694471fb2`.

## Correlation and safe evidence

Provide the correlation ID, operation, safe error code, and bounded status. Valid inbound IDs are
preserved; unsafe values are replaced.

Never include in an issue or screenshot:

- session cookies or opaque session values;
- provider access, ID, or refresh tokens;
- client secrets or wrapping-key material;
- credential-bearing database/Redis URLs;
- raw audit payloads or scanner secret evidence.

A correlation ID is diagnostic metadata, not authorization or an infrastructure identifier.
