# Portal API contract

## Versioning

`/v1` is the only supported business API prefix in PR-PORTAL-001. Health endpoints remain
unversioned operational contracts. Additive compatible changes may be made within `v1`; breaking
changes require a new major route and a documented coexistence window.

The semantic contract version is returned by system information and readiness. It is independent
from the Portal API application version.

## Source of truth and generation

FastAPI response models and route declarations are authoritative:

```bash
make portal-openapi
make portal-client
make portal-contract-check
```

The first command creates `packages/portal-contracts/openapi/portal-api-v1.json`. The second uses
`@hey-api/openapi-ts` to create `packages/portal-contracts/src/generated/`. Both outputs are
reviewed and committed. Generated files are never edited by hand. CI regenerates both artifacts and
fails on drift.

Portal Web calls the generated SDK through a small transport policy wrapper. It does not maintain
duplicate request or response interfaces.

## Endpoints

| Endpoint | Meaning |
| --- | --- |
| `GET /health/live` | Process-local liveness; never depends on optional infrastructure. |
| `GET /health/ready` | `READY`, `DEGRADED`, or `NOT_READY`; `NOT_READY` returns HTTP 503. |
| `GET /v1/system/info` | Safe application, contract, build, and environment metadata. |
| `GET /v1/system/dependencies` | Truthful state for explicitly registered adapters. |

## Problem Details

Errors use the checked-in `ProblemDetails` schema with `type`, `title`, `status`, `detail`,
`instance`, `error_code`, `correlation_id`, and `timestamp`. Optional fields are safe and bounded.
The initial stable codes are:

- `PORTAL_INTERNAL_ERROR`
- `INVALID_REQUEST`
- `RESOURCE_NOT_FOUND`
- `METHOD_NOT_ALLOWED`
- `DEPENDENCY_UNAVAILABLE`
- `DEPENDENCY_TIMEOUT`
- `CONFIGURATION_ERROR`
- `RATE_LIMITED`
- `SERVICE_NOT_READY`
- `UNSUPPORTED_API_VERSION`

Unknown exceptions become a sanitized internal error. Python traces, SQL, local paths, secrets, and
upstream response bodies are never placed in the contract.

## Correlation

Clients may send `X-Correlation-ID` using 1–128 alphanumeric, dot, underscore, colon, or hyphen
characters. Invalid values are replaced. Every response, Problem Details body, health response, and
request log includes the effective identifier. `X-Request-ID` is a distinct per-BFF-request value.
