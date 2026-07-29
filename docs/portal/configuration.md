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
| `PORTAL_API_SECRET_PROVIDER` | `environment` | yes | no | Bounded provider ID; environment adapter is forbidden for secret-bearing production features |
| `PORTAL_API_SECURITY_RUNTIME_ENABLED` | `false` | no | no | Supported only in local, test and development; staging/production authorization remains deferred |
| `PORTAL_API_DATABASE_URL` | none | with security runtime and environment provider | yes | PostgreSQL URL resolved only by the environment secret-provider input model |
| `PORTAL_API_SECURITY_MASTER_KEY` | none | when local/development security runtime is enabled | yes | Base64url-encoded 256-bit key; keep stable across normal restart |
| `PORTAL_API_SECURITY_KEY_VERSION` | `local-development-v1` | with security master key | no | Bounded identifier attached to derived lookup keys and protected envelopes |
| `PORTAL_API_SECURITY_PREVIOUS_MASTER_KEY` | none | during a controlled key transition | yes | Previous 256-bit key; accepted only inside the declared transition window |
| `PORTAL_API_SECURITY_PREVIOUS_KEY_VERSION` | none | with previous key | no | Explicit version of the previous key; must differ from the current version |
| `PORTAL_API_SECURITY_KEY_TRANSITION_STARTED_AT` | none | with previous key | no | Timezone-aware start of previous-key acceptance |
| `PORTAL_API_SECURITY_KEY_TRANSITION_EXPIRES_AT` | none | with previous key | no | Exclusive deterministic expiry; the window cannot exceed one absolute session lifetime |
| `PORTAL_API_SECURITY_FUTURE_KEY_VERSION` | none | no | no | Metadata-only staged version; must differ from current and previous versions |
| `PORTAL_API_OIDC_PROVIDER_ID` | `local-keycloak` | with security runtime | no | Bounded provider identity |
| `PORTAL_API_OIDC_ISSUER` | local Keycloak realm | with security runtime | no | Exact configured issuer; non-local environments require HTTPS |
| `PORTAL_API_OIDC_DISCOVERY_URL` | issuer discovery path | no | no | Absolute HTTP(S) URL; exact issuer validation remains mandatory |
| `PORTAL_API_OIDC_CLIENT_ID` | `fintech-portal` | with security runtime | no | Confidential client identity |
| `PORTAL_API_OIDC_CLIENT_SECRET` | none | with security runtime and environment provider | yes | Resolved only through the selected secret provider |
| `PORTAL_API_OIDC_REDIRECT_URI` | local Portal callback | with security runtime | no | Absolute HTTP(S) callback URL |
| `PORTAL_API_OIDC_SCOPES` | `openid profile` | yes | no | Whitespace-separated; must contain `openid` |
| `PORTAL_API_OIDC_ALLOWED_ALGORITHMS` | `RS256` | yes | no | Comma-separated allowlist |
| `PORTAL_API_OIDC_GROUP_CLAIM_PATH` | `groups` | yes | no | Provider group-claim path |
| `PORTAL_API_OIDC_ALLOWED_ROLES` | Portal viewer roles | yes | no | Comma-separated provider-role allowlist |
| `PORTAL_API_ALLOWED_ENVIRONMENT_IDS` | `local,development` | yes | no | Comma-separated environment authority |
| `PORTAL_API_PORTAL_TENANT_ID` | `fintech-platform-primary` | yes | no | Stable tenant identity |
| `PORTAL_API_IDENTITY_MAPPING_REVISION` | `local-mapping-v1` | yes | no | Identity mapping revision |
| `PORTAL_API_CALLBACK_POLICY_REVISION` | `local-callback-policy-v1` | yes | no | Local/test/development policy only; production policy remains deferred |
| `PORTAL_API_CAPABILITY_REVISION` | `local-capability-v1` | yes | no | Capability mapping revision |
| `PORTAL_API_SESSION_SECURITY_EPOCH` | `1` | yes | no | Positive durable revocation epoch |
| `PORTAL_API_SESSION_IDLE_TTL_SECONDS` | `1800` | yes | no | At most 1800 and no longer than the absolute lifetime |
| `PORTAL_API_SESSION_ABSOLUTE_TTL_SECONDS` | `28800` | yes | no | At most 28800 |
| `PORTAL_API_IDENTITY_FRESHNESS_SECONDS` | `900` | yes | no | At most 900 |
| `PORTAL_API_SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS` | `60` | yes | no | At most 60 |
| `PORTAL_API_MAXIMUM_ACTIVE_SESSIONS` | `5` | yes | no | At most 5 |
| `PORTAL_API_OIDC_HTTP_TIMEOUT_SECONDS` | `5` | yes | no | Greater than 0, at most 30 |
| `PORTAL_API_OIDC_CACHE_TTL_SECONDS` | `900` | yes | no | Fixed validated discovery/JWKS cache TTL |
| `PORTAL_API_OIDC_STALE_CEILING_SECONDS` | `3600` | yes | no | Fixed fail-closed stale ceiling |
| `PORTAL_API_ALLOWED_RETURN_PATHS` | `/,/system-status` | yes | no | Comma-separated local absolute paths |
| `PORTAL_API_LOGIN_INTENT_TTL_SECONDS` | `300` | yes | no | At most 300 |
| `PORTAL_API_LOGIN_TRANSACTION_TTL_SECONDS` | `300` | yes | no | At most 300 |
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
| `PORTAL_API_ABUSE_PROTECTION_ENABLED` | `false` | no | no | Enables Redis-backed enforcement and bounded operation-specific fallbacks |
| `PORTAL_API_REDIS_URL` | none | when abuse protection enabled | yes | Host execution uses the published Redis port; Compose uses `redis://portal-redis:6379/0`; production requires `rediss://` |
| `PORTAL_API_REDIS_CONNECT_TIMEOUT_SECONDS` | `0.5` | no | no | Greater than 0 and at most 5 seconds |
| `PORTAL_API_REDIS_OPERATION_TIMEOUT_SECONDS` | `0.25` | no | no | Greater than 0 and at most 5 seconds |
| `PORTAL_API_REDIS_MAX_CONNECTIONS` | `50` | no | no | 1-500 |
| `PORTAL_API_REDIS_KEY_PREFIX` | `portal:abuse` | no | no | Bounded safe namespace |
| `PORTAL_API_ABUSE_POLICY_VERSION` | `development-v1` | no | no | Bounded policy revision; defaults require production tuning |
| `PORTAL_API_CLIENT_ADDRESS_HMAC_SECRET` | none | when abuse protection enabled | yes | Base64url-encoded 256-bit key, distinct per environment |
| `PORTAL_API_FORWARDED_HEADER_MODE` | `direct` | no | no | `direct` or `x_forwarded_for` |
| `PORTAL_API_TRUSTED_PROXY_CIDRS` | empty | in proxy mode | no | Canonical explicit CIDRs; catch-all ranges forbidden |
| `PORTAL_API_MAX_FORWARDED_HOPS` | `5` | no | no | 1-16 |
| `PORTAL_API_IPV4_PREFIX_LENGTH` | `24` | no | no | 16-32 |
| `PORTAL_API_IPV6_PREFIX_LENGTH` | `64` | no | no | 32-128 |
| `PORTAL_API_ABUSE_LOCAL_FALLBACK_MAX_KEYS` | `10000` | no | no | 100-100000 bounded process-local keys |
| `PORTAL_API_ABUSE_PROVIDER_MAX_CONCURRENCY` | `20` | no | no | 1-500 leased provider calls |
| `PORTAL_API_ABUSE_PROVIDER_CONCURRENCY_LEASE_SECONDS` | `15` | no | no | 1-120 seconds |
| `PORTAL_API_ABUSE_BACKEND_AUDIT_INTERVAL_SECONDS` | `60` | no | no | 1-3600 seconds |
| `PORTAL_API_AUDIT_OUTBOX_ENABLED` | `false` | no | no | Enables the separate audit worker; Compose enables it locally |
| `PORTAL_API_AUDIT_WORKER_DATABASE_URL` | none | when outbox enabled | yes | PostgreSQL URL for the least-privileged `portal_archive` role |
| `PORTAL_API_AUDIT_OUTBOX_POLL_INTERVAL_SECONDS` | `1` | no | no | 0.1-60 seconds |
| `PORTAL_API_AUDIT_OUTBOX_BATCH_SIZE` | `100` | no | no | 1-500 records per claim |
| `PORTAL_API_AUDIT_OUTBOX_WORKER_CONCURRENCY` | `4` | no | no | 1-32 concurrent deliveries |
| `PORTAL_API_AUDIT_OUTBOX_LEASE_SECONDS` | `30` | no | no | 5-600 seconds and greater than delivery timeout |
| `PORTAL_API_AUDIT_OUTBOX_MAX_ATTEMPTS` | `5` | no | no | 1-25 attempts |
| `PORTAL_API_AUDIT_OUTBOX_BASE_BACKOFF_SECONDS` | `1` | no | no | 0.1-300 seconds |
| `PORTAL_API_AUDIT_OUTBOX_MAX_BACKOFF_SECONDS` | `60` | no | no | 1-3600 seconds and not below base delay |
| `PORTAL_API_AUDIT_OUTBOX_DESTINATION` | `local_postgres` | no | no | Only the bounded built-in destination is currently accepted |
| `PORTAL_API_AUDIT_OUTBOX_DELIVERY_TIMEOUT_SECONDS` | `5` | no | no | Greater than 0, at most 60 seconds |
| `PORTAL_API_AUDIT_OUTBOX_RETENTION_DAYS` | `7` | no | no | Delivered mutable-state retention |
| `PORTAL_API_AUDIT_DEAD_LETTER_RETENTION_DAYS` | `30` | no | no | Cannot be shorter than delivered retention |
| `PORTAL_API_MAINTENANCE_ENABLED` | `true` | no | no | Runs bounded jobs in the audit worker |
| `PORTAL_API_MAINTENANCE_INTERVAL_SECONDS` | `60` | no | no | 1-3600 seconds |
| `PORTAL_API_MAINTENANCE_BATCH_SIZE` | `100` | no | no | 1-1000 rows per action |
| `PORTAL_API_MAINTENANCE_MAX_RUNTIME_SECONDS` | `30` | no | no | 1-300 seconds per job |
| `PORTAL_API_REPLAY_RETENTION_BUFFER_SECONDS` | `300` | no | no | 0-86400 seconds after replay expiry |
| `PORTAL_API_TERMINAL_ENVELOPE_RETENTION_DAYS` | `7` | no | no | Crypto-erased terminal envelopes only |

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

See [Distributed abuse protection](abuse-protection.md) before configuring trusted proxies,
production Redis, operation policies, or degradation behavior.

See [Audit outbox delivery and background maintenance](audit-outbox-and-maintenance.md) before
configuring archive-role credentials, delivery retry, retention, or maintenance schedules.

See [Portal secrets provider boundary](secrets-provider-boundary.md) for logical references,
provider lifecycle, startup resolution, production restrictions, and bounded rotation.

## Portal Web

| Variable | Default | Phase | Secret |
| --- | --- | --- | --- |
| `PORTAL_WEB_PORT` | `3000` | runtime port mapping | no |
| `PORTAL_WEB_HOST` | `0.0.0.0` | server runtime | no |
| `PORTAL_API_INTERNAL_URL` | `http://127.0.0.1:8010` | server runtime | no |
| `PORTAL_PUBLIC_ORIGIN` | `http://localhost:3000` | server runtime | no |
| `PORTAL_IDP_PUBLIC_URL` | `http://portal-idp.localhost:8081` | build/CSP | no |
| `NEXT_PUBLIC_PORTAL_ENV` | `local` | build | no |
| `NEXT_PUBLIC_PORTAL_WEB_VERSION` | `0.1.0-dev` | build | no |
| `NEXT_PUBLIC_PORTAL_BUILD_SHA` | `local` | build | no |

Only the three explicitly safe build labels are public. The API target is server-only and the
browser always calls relative `/portal-api/*` paths. No token, credential, database URL, or
telemetry secret may be introduced with a `NEXT_PUBLIC_*` name.

## S06-03 configuration architecture

The flat `PORTAL_API_*` namespace remains the compatibility boundary. It is parsed once and
converted to immutable domain models. API, audit-worker and migration composition have separate
role contracts. Raw environment secret values remain in `EnvironmentSecretInputs` and are consumed
only by `EnvironmentSecretProvider`; runtime role aggregates contain provider and logical-reference
metadata only.

```text
Environment/Profile + Process Role
                |
                v
      Deterministic Config Loader
                |
                v
       Frozen Typed Aggregate
       /        |          \
     API      Worker     Migration
       \        |          /
                v
       Secret Reference Catalog
                |
                v
          Secret Provider
                |
                v
        Runtime Composition
```

Unknown `PORTAL_API_*` names fail deterministically in test, staging and production. Local and
development retain compatibility and emit a safe warning containing variable names only. Unrelated
operating-system variables are ignored.

Portal Web uses three distinct typed surfaces:

- `NEXT_PUBLIC_*` build labels are browser-safe and public;
- server-runtime URLs and host/port values never enter the public configuration object;
- Playwright/E2E flags are test-only configuration.

### Environment profiles

| Profile | Behavior |
| --- | --- |
| `local` | Safe local defaults; unknown Portal names warn |
| `test` | Deterministic isolated validation; unknown Portal names fail |
| `development` | HTTPS is required for external OIDC; unknown Portal names warn |
| `staging` | HTTPS CORS and strict unknown-name rejection; security runtime remains blocked |
| `production` | JSON logs, immutable build identity, HTTPS, strict unknown-name rejection; security runtime remains blocked |

### Decision log

- Configuration models are frozen after startup; dynamic reload is not part of S06-03.
- Process roles receive narrow immutable aggregates while the flat loader remains a compatibility
  facade.
- Existing environment names are preserved and exposed through a machine-tested registry.
- Secret values are excluded from role aggregates and diagnostics.
- Staging/production reject unknown Portal-prefixed names; local/development warn.
- No production callback or abuse policy is introduced by this task.

### Production blockers

Production configuration contract implemented; production security runtime authorization remains
deferred. The security runtime guard, local/test/development callback policy and development abuse
policy remain enforced and visible. A syntactically valid production configuration is not a fully
operational production security runtime.
