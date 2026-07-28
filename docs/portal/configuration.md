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
| `PORTAL_API_TELEMETRY_ENABLED` | `false` | no | no | Uses the no-op recorder when disabled |
| `PORTAL_API_TELEMETRY_METRICS_EXPORTER` | `prometheus` | when telemetry enabled | no | `none`, `console`, `otlp`, `prometheus` |
| `PORTAL_API_TELEMETRY_TRACE_EXPORTER` | `none` | when telemetry enabled | no | `none`, `console`, `otlp` |
| `PORTAL_API_TELEMETRY_TRACE_SAMPLING_RATIO` | `0.1` | no | no | 0 through 1; parent-based |
| `PORTAL_API_TELEMETRY_EXPORT_INTERVAL_SECONDS` | `30` | no | no | 1 through 300; periodic exporters only |
| `PORTAL_API_TELEMETRY_EXPORT_TIMEOUT_SECONDS` | `10` | no | no | Greater than 0, at most 30 |
| `PORTAL_API_TELEMETRY_OTLP_ENDPOINT` | `http://localhost:4318` | for OTLP | no | Absolute HTTP(S) base URL; production HTTPS only; embedded credentials forbidden |
| `PORTAL_API_TELEMETRY_PROMETHEUS_HOST` | `127.0.0.1` | for Prometheus | no | Compose sets `0.0.0.0` inside the container |
| `PORTAL_API_TELEMETRY_PROMETHEUS_PORT` | `9464` | for Prometheus | no | 1024 through 65535 |
| `PORTAL_API_TELEMETRY_RESOURCE_ATTRIBUTES` | empty | no | no | At most 16 bounded `key=value` pairs; secret-like keys forbidden |
| `PORTAL_API_OPENAPI_ENABLED` | `true` | no | no | Must be false in production |
| `PORTAL_API_DEVELOPMENT_IDENTITY_ENABLED` | `false` | no | no | Forbidden in production; no identity behavior exists yet |
| `PORTAL_API_SECURITY_MASTER_KEY` | none | when local/development security runtime is enabled | yes | Base64url-encoded 256-bit key; keep stable across normal restart |
| `PORTAL_API_SECURITY_KEY_VERSION` | `local-development-v1` | with security master key | no | Bounded identifier attached to derived lookup keys and protected envelopes |
| `PORTAL_API_SECURITY_PREVIOUS_MASTER_KEY` | none | during a controlled key transition | yes | Previous 256-bit key; accepted only inside the declared transition window |
| `PORTAL_API_SECURITY_PREVIOUS_KEY_VERSION` | none | with previous key | no | Explicit version of the previous key; must differ from the current version |
| `PORTAL_API_SECURITY_KEY_TRANSITION_STARTED_AT` | none | with previous key | no | Timezone-aware start of previous-key acceptance |
| `PORTAL_API_SECURITY_KEY_TRANSITION_EXPIRES_AT` | none | with previous key | no | Exclusive deterministic expiry; the window cannot exceed one absolute session lifetime |
| `PORTAL_API_PROVIDER_REFRESH_ENABLED` | `true` | no | no | Starts the non-blocking provider refresh worker when the security runtime is active |
| `PORTAL_API_PROVIDER_REFRESH_THRESHOLD_SECONDS` | `120` | no | no | Proactive refresh window; 30-600 seconds |
| `PORTAL_API_PROVIDER_REFRESH_SCAN_INTERVAL_SECONDS` | `5` | no | no | Durable due-work scan interval; 1-60 seconds |
| `PORTAL_API_PROVIDER_REFRESH_RETRY_BUDGET` | `3` | no | no | Maximum bounded ambiguous failures before the session fails closed; 1-10 |
| `PORTAL_API_PROVIDER_REFRESH_INITIAL_BACKOFF_SECONDS` | `1` | no | no | Initial retry delay; 0.1-30 seconds |
| `PORTAL_API_PROVIDER_REFRESH_MAX_BACKOFF_SECONDS` | `30` | no | no | Exponential-backoff ceiling; 1-300 seconds and not below the initial delay |
| `PORTAL_API_PROVIDER_REFRESH_LEASE_SECONDS` | `30` | no | no | Database refresh-claim lease; 10-300 seconds and greater than provider timeouts |
| `PORTAL_API_PROVIDER_REFRESH_BATCH_SIZE` | `25` | no | no | Maximum claims per worker scan; 1-100 |
| `PORTAL_API_PROVIDER_LOGOUT_TIMEOUT_SECONDS` | `5` | no | no | Per-provider cleanup operation timeout; greater than 0 and at most 30 seconds |
| `PORTAL_API_PROVIDER_LOGOUT_REPLAY_TTL_SECONDS` | `86400` | no | no | Durable back-channel logout-token replay fence; 300-86400 seconds |

The test environment may omit the master key to obtain isolated ephemeral authority. Local and
development security runtimes require an explicit key so valid sessions and pending authentication
state remain recoverable across normal process restart. The root `.env.example` value is disposable
local-only material and must be replaced in an ignored `.env` before shared development.

A rotation selects the new key and version as current, while the old key and version may be
configured as previous with an explicit start and exclusive expiry. All four previous-key
settings are required together. During that bounded window, existing login intents, callbacks,
sessions, CSRF authority, and protected values may be validated with the previous key; all newly
created state uses the current key. At or after expiry, previous and unknown versions fail closed.
Rollback during the window requires an explicit configuration reversal: select the former key as
current and retain the other version as previous for the remainder of the same bounded window.

Validate without starting the server:

```bash
make portal-config-check
```

See [Portal telemetry and observability](observability.md) for the metrics catalog, trace model,
exporter setup, and operational validation.

See [Provider-backed session lifecycle](provider-session-lifecycle.md) for state transitions,
provider configuration, refresh/revocation/logout operations, and incident procedures.

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
