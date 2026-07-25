# Portal configuration

All values are loaded at process or build start. Root `.env.example` contains non-secret local
examples. Production values must be supplied by the deployment environment; secrets must not use
`NEXT_PUBLIC_*`.

## Portal API

| Variable | Default | Required | Secret | Constraint |
| --- | --- | --- | --- | --- |
| `PORTAL_API_ENVIRONMENT` | `local` | yes | no | `local`, `test`, `development`, `staging`, `production` |
| `PORTAL_API_SERVICE_NAME` | `portal-api` | no | no | Safe service identifier |
| `PORTAL_API_SERVICE_VERSION` | `0.1.0-dev` | yes | no | Inject immutable release version in production |
| `PORTAL_API_API_VERSION` | `v1` | yes | no | Foundation accepts only `v1` |
| `PORTAL_API_CONTRACT_VERSION` | `1.0.0` | yes | no | OpenAPI semantic version |
| `PORTAL_API_DOCUMENTATION_VERSION` | `portal-foundation-v1` | yes | no | Documentation contract |
| `PORTAL_API_BUILD_SHA` | `local` | yes | no | Must not be `local` in production |
| `PORTAL_API_BUILD_TIME` | `local` | yes | no | Must not be `local` in production |
| `PORTAL_API_LOG_LEVEL` | `INFO` | no | no | Python logging level |
| `PORTAL_API_LOG_FORMAT` | `console` | yes | no | Production requires `json` |
| `PORTAL_API_HOST` | `127.0.0.1` | yes | no | Compose sets `0.0.0.0` |
| `PORTAL_API_PORT` | `8010` | yes | no | 1–65535 |
| `PORTAL_API_ALLOWED_ORIGINS` | `http://localhost:3000` | yes | no | Explicit comma-separated origins; production HTTPS only |
| `PORTAL_API_TRUSTED_HOSTS` | `localhost,127.0.0.1,portal-api` | yes | no | Wildcard forbidden in production |
| `PORTAL_API_DEPENDENCY_TIMEOUT_SECONDS` | `2` | yes | no | Greater than 0, at most 30 |
| `PORTAL_API_READINESS_TIMEOUT_SECONDS` | `5` | yes | no | Greater than 0, at most 60 |
| `PORTAL_API_HEALTH_CACHE_TTL_SECONDS` | `2` | yes | no | 0–60 |
| `PORTAL_API_TELEMETRY_ENABLED` | `false` | no | no | Uses the no-op recorder until a safe exporter is configured |
| `PORTAL_API_OPENAPI_ENABLED` | `true` | no | no | Must be false in production |
| `PORTAL_API_DEVELOPMENT_IDENTITY_ENABLED` | `false` | no | no | Forbidden in production; no identity behavior exists yet |

Validate without starting the server:

```bash
make portal-config-check
```

## Portal Web

| Variable | Default | Phase | Secret |
| --- | --- | --- | --- |
| `PORTAL_WEB_PORT` | `3000` | runtime port mapping | no |
| `PORTAL_API_INTERNAL_URL` | `http://127.0.0.1:8010` | server runtime | no |
| `NEXT_PUBLIC_PORTAL_ENV` | `local` | build | no |
| `NEXT_PUBLIC_PORTAL_WEB_VERSION` | `0.1.0-dev` | build | no |
| `NEXT_PUBLIC_PORTAL_BUILD_SHA` | `local` | build | no |

Only the three explicitly safe build labels are public. The API target is server-only and the
browser always calls relative `/portal-api/*` paths. No token, credential, database URL, or
telemetry secret may be introduced with a `NEXT_PUBLIC_*` name.
