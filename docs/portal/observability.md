# Portal telemetry and observability

The Portal API uses the OpenTelemetry API and SDK as its vendor-neutral telemetry boundary.
Telemetry is initialized once in the application lifespan and shut down with a bounded flush.
When telemetry is disabled, the runtime uses an allocation-light no-op recorder and starts no
exporter thread or metrics listener.

## Architecture

```text
HTTP request (W3C trace context)
  |
  +-- server span: METHOD route
      |
      +-- authentication flow
      |   +-- OIDC discovery/JWKS/token client spans
      |   +-- token-validation internal span
      |
      +-- SQL client spans
      +-- readiness dependency client spans
      +-- audit-persistence internal span

Instrumentation
  +-- counters, histograms, observable gauges
  +-- structured logs with request_id, trace_id, span_id
  +-- resource: service name/version/instance, deployment environment, build SHA

Export
  +-- metrics: Prometheus, OTLP/HTTP, console, or none
  +-- traces: OTLP/HTTP, console, or none
```

The metrics and trace exporters are selected independently. This allows Prometheus pull metrics
with OTLP traces, a single OTLP pipeline, console output during development, or a fully disabled
runtime.

### Correlation

- Inbound W3C `traceparent` is extracted before the HTTP server span starts.
- Outbound OIDC calls receive the current W3C trace context.
- API responses include `X-Request-ID`, `X-Trace-ID`, and `X-Span-ID`.
- Structured logs include `request_id`, `correlation_id`, `trace_id`, and `span_id`.
- Sampled trace context is attached to metric measurements as OpenTelemetry exemplars when the
  configured exporter supports exemplars.
- Request, trace, span, session, user, key, and token identifiers are never metric labels. This
  prevents cardinality growth and credential leakage.

## Design decisions

### Metric naming and labels

Logical metric names use the `portal.<subsystem>.<measurement>` convention. Exporters may translate
dots or append unit/type suffixes; for example, Prometheus exports
`portal.http.requests` as `portal_http_requests_total`.

Labels are bounded enums or controlled identifiers:

- HTTP: method, route template, status code or bounded error class.
- Readiness: registered dependency identifier and bounded health state.
- OIDC: operation, outcome, and validation category.
- Database: SQL operation (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CALL`, `OTHER`) and outcome.
- Audit/auth/session: declared event names and bounded outcomes.

Raw URLs, SQL statements, exception messages, subject identifiers, request IDs, trace IDs, key IDs,
and untrusted provider values are not labels.

### Sampling

Tracing uses parent-based ratio sampling. An inbound sampling decision is preserved; new root traces
use `PORTAL_API_TELEMETRY_TRACE_SAMPLING_RATIO`. Metrics are not sampled. A value of `0` disables
new root span recording without disabling metrics, while `1` records every new trace.

### Performance

- Batch span export is used for configured production trace exporters.
- Metric export is periodic for OTLP/console and pull-based for Prometheus.
- Database query instrumentation records only a bounded operation name, never SQL text or bind data.
- Observable database/session gauges catch collection errors and omit a sample instead of affecting
  application behavior.
- Readiness state is maintained in process with one bounded record per registered dependency.
- Telemetry-disabled mode allocates no SDK provider, exporter, server, or background worker.

## Metrics catalog

All names below are logical OpenTelemetry names.

| Name | Type | Unit | Labels | Purpose |
| --- | --- | --- | --- | --- |
| `portal.http.requests` | Counter | `{request}` | `http.request.method`, `http.route`, `http.response.status_code` | HTTP request volume |
| `portal.http.errors` | Counter | `{error}` | `http.request.method`, `http.route`, `error.type` | HTTP 4xx/5xx volume |
| `portal.http.duration` | Histogram | `ms` | method, route, status code | HTTP latency |
| `portal.auth.events` | Counter | `{event}` | `event`, `outcome` | Login attempts/success/failure, callback success/failure, logout, refresh |
| `portal.session.events` | Counter | `{event}` | `event`, `outcome` | Session creation, expiration, revocation, rotation, refresh |
| `portal.session.active` | Observable gauge | `{session}` | none | Active and refresh-required durable sessions |
| `portal.readiness.checks` | Counter | `{check}` | `status` | Overall readiness evaluations |
| `portal.readiness.overall` | Observable gauge | `1` | none | Current readiness: 1 ready, 0 otherwise |
| `portal.readiness.dependency.checks` | Counter | `{check}` | `dependency`, `status` | Dependency check outcomes |
| `portal.readiness.dependency.duration` | Histogram | `ms` | `dependency`, `status` | Dependency-check latency |
| `portal.readiness.dependency.status` | Gauge | `1` | `dependency` | Latest state: 1 up, 0 otherwise |
| `portal.readiness.dependency.timeouts` | Counter | `{timeout}` | `dependency` | Dependency timeouts |
| `portal.abuse.requests` | Counter | `{request}` | operation, policy, policy version | Abuse evaluation volume |
| `portal.abuse.decisions` | Counter | `{decision}` | operation, decision, dimension | Bounded allow/throttle/block outcomes |
| `portal.abuse.backend.duration` | Histogram | `ms` | operation, backend status | Distributed/fallback evaluation latency |
| `portal.abuse.backend.failures` | Counter | `{failure}` | operation, failure class | Redis timeouts and unavailability |
| `portal.abuse.penalties` | Counter | `{transition}` | operation, penalty level | Bounded temporary penalty outcomes |
| `portal.abuse.fallback.activations` | Counter | `{activation}` | operation, fallback mode | Process-local fallback activation |
| `portal.abuse.provider.concurrency` | Histogram | `ms` | provider operation, outcome | Provider lease acquisition |
| `portal.abuse.provider.throttled` | Counter | `{operation}` | provider operation | Rejected provider outbound work |
| `portal.readiness.dependency.transitions` | Counter | `{transition}` | `dependency`, `from`, `to` | Dependency state transitions |
| `portal.readiness.dependency.recovery` | Histogram | `s` | `dependency` | Failure-to-recovery duration |
| `portal.readiness.dependency.availability` | Observable gauge | `1` | `dependency` | Process-lifetime successful-check ratio |
| `portal.readiness.dependency.failure.duration` | Observable gauge | `s` | `dependency` | Current continuous failure duration |
| `portal.oidc.operation.duration` | Histogram | `ms` | `operation`, `outcome` | Discovery, JWKS, and token latency |
| `portal.oidc.cache.refreshes` | Counter | `{refresh}` | `operation` | Discovery/JWKS cache refreshes |
| `portal.oidc.unknown_kid.refreshes` | Counter | `{refresh}` | none | Forced JWKS refresh on unknown signing key |
| `portal.oidc.validation.failures` | Counter | `{failure}` | `category` | Fail-closed token validation failures |
| `portal.provider.session.operations` | Counter | `{operation}` | `operation`, `outcome` | Refresh, provider logout, revocation, and disposal outcomes |
| `portal.provider.session.duration` | Histogram | `ms` | `operation`, `outcome` | Provider lifecycle operation latency |
| `portal.provider.session.retries` | Counter | `{retry}` | `operation` | Bounded provider refresh retry attempts |
| `portal.provider.session.transitions` | Counter | `{transition}` | `from`, `to` | Durable provider-session state transitions |
| `portal.db.connection.wait` | Histogram | `ms` | `outcome` | Pool checkout and connection acquisition wait |
| `portal.db.connection.failures` | Counter | `{failure}` | `outcome` | Connection acquisition failures |
| `portal.db.query.duration` | Histogram | `ms` | `operation`, `outcome` | Bounded query latency |
| `portal.db.pool.connections` | Observable gauge | `{connection}` | `state` | Pool size, checked-in/out, and overflow |
| `portal.audit.events` | Counter | `{event}` | `event`, `outcome` | Audit ledger write outcomes |
| `portal.audit.outbox.backlog` | Observable gauge | `{event}` | `scope=process` | Process-observed outbox backlog delta |
| `portal.audit.archive` | Counter | `{event}` | `outcome` | Archive delivery success/failure |

## Trace model

| Span | Kind | Parent | Important attributes |
| --- | --- | --- | --- |
| `METHOD route` | SERVER | extracted remote context or root | method, route, status, request ID |
| `readiness.<dependency>` | CLIENT | HTTP readiness span | dependency |
| `oidc.discovery` | CLIENT | current authentication/request span | OIDC operation |
| `oidc.jwks` | CLIENT | current authentication/request span | OIDC operation |
| `oidc.token` | CLIENT | current authentication/request span | OIDC operation |
| `provider.session.refresh` | INTERNAL | refresh worker context | lifecycle state, bounded retry count |
| `provider.session.provider_logout` | INTERNAL | logout request span | bounded operation |
| `provider.session.access_revocation` | INTERNAL | logout request span | bounded operation |
| `provider.session.refresh_revocation` | INTERNAL | logout request span | bounded operation |
| `provider.session.backchannel_logout` | INTERNAL | provider request span | bounded operation |
| `token.validate` | INTERNAL | current callback/request span | validation stage |
| `db.select`, `db.insert`, etc. | CLIENT | current operation span | database system, bounded operation |
| `portal.audit.persist` | INTERNAL | current operation span | audit persistence boundary |

Exceptions set span error status without adding raw secrets or SQL statements as attributes.

## Configuration reference

| Variable | Default | Constraint |
| --- | --- | --- |
| `PORTAL_API_TELEMETRY_ENABLED` | `false` | When false, uses no-op telemetry |
| `PORTAL_API_TELEMETRY_METRICS_EXPORTER` | `prometheus` | `none`, `console`, `otlp`, `prometheus` |
| `PORTAL_API_TELEMETRY_TRACE_EXPORTER` | `none` | `none`, `console`, `otlp` |
| `PORTAL_API_TELEMETRY_TRACE_SAMPLING_RATIO` | `0.1` | 0 through 1 |
| `PORTAL_API_TELEMETRY_EXPORT_INTERVAL_SECONDS` | `30` | 1 through 300 |
| `PORTAL_API_TELEMETRY_EXPORT_TIMEOUT_SECONDS` | `10` | Greater than 0, at most 30 |
| `PORTAL_API_TELEMETRY_OTLP_ENDPOINT` | `http://localhost:4318` | Absolute HTTP(S) base URL; production requires HTTPS; embedded credentials forbidden |
| `PORTAL_API_TELEMETRY_PROMETHEUS_HOST` | `127.0.0.1` | Compose sets `0.0.0.0` inside the container |
| `PORTAL_API_TELEMETRY_PROMETHEUS_PORT` | `9464` | 1024 through 65535 |
| `PORTAL_API_TELEMETRY_RESOURCE_ATTRIBUTES` | empty | Up to 16 comma-separated `key=value` pairs; secret-like keys rejected |
| `PORTAL_API_SERVICE_VERSION` | `0.1.0-dev` | Exported as `service.version` |
| `PORTAL_API_ENVIRONMENT` | `local` | Exported as `deployment.environment.name` |

At least one metrics or trace exporter must be active when telemetry is enabled.

## Operational runbook

### Enable Prometheus metrics locally

Set the following in the ignored `.env`, then recreate the API:

```dotenv
PORTAL_API_TELEMETRY_ENABLED=true
PORTAL_API_TELEMETRY_METRICS_EXPORTER=prometheus
PORTAL_API_TELEMETRY_TRACE_EXPORTER=none
PORTAL_API_TELEMETRY_PROMETHEUS_HOST=0.0.0.0
PORTAL_API_TELEMETRY_PROMETHEUS_PORT=9464
```

```bash
docker compose --env-file .env.example up -d --build portal-api
curl --fail http://127.0.0.1:9464/metrics
```

Prometheus scrape configuration:

```yaml
scrape_configs:
  - job_name: portal-api
    static_configs:
      - targets: ["portal-api:9464"]
```

The Compose port is bound to loopback by default. Expose it beyond the host only through approved
monitoring network controls.

### Connect an OpenTelemetry Collector

```dotenv
PORTAL_API_TELEMETRY_ENABLED=true
PORTAL_API_TELEMETRY_METRICS_EXPORTER=otlp
PORTAL_API_TELEMETRY_TRACE_EXPORTER=otlp
PORTAL_API_TELEMETRY_OTLP_ENDPOINT=http://otel-collector:4318
PORTAL_API_TELEMETRY_TRACE_SAMPLING_RATIO=0.1
```

The base endpoint is expanded to `/v1/metrics` and `/v1/traces`. Use HTTPS in production. Configure
authentication, certificate trust, and network policy at the deployment/collector boundary.

### Console development mode

```dotenv
PORTAL_API_TELEMETRY_ENABLED=true
PORTAL_API_TELEMETRY_METRICS_EXPORTER=console
PORTAL_API_TELEMETRY_TRACE_EXPORTER=console
PORTAL_API_TELEMETRY_TRACE_SAMPLING_RATIO=1
```

Console mode is intentionally verbose and is not a production default.

### Validate

1. Call `GET /health/ready`.
2. Confirm response correlation headers are present.
3. Confirm JSON logs contain the same request, trace, and span context.
4. Scrape Prometheus or inspect the Collector for `portal.readiness.*` and `portal.http.*`.
5. Stop PostgreSQL or the OIDC provider in a controlled local environment and verify readiness,
   dependency transition, failure-duration, and recovery metrics.

### Troubleshooting

- **API starts but no telemetry appears:** confirm `PORTAL_API_TELEMETRY_ENABLED=true` and that an
  exporter other than `none` is selected.
- **Prometheus connection refused:** the metrics listener is separate from port 8010; confirm port
  9464 mapping and `PORTAL_API_TELEMETRY_PROMETHEUS_HOST=0.0.0.0` in the container.
- **OTLP export fails:** confirm the Collector uses OTLP/HTTP on port 4318 and the configured base URL
  does not already include `/v1/traces` or `/v1/metrics`.
- **No traces but metrics work:** check the trace exporter and sampling ratio. Parent-based sampling
  preserves an upstream unsampled decision.
- **High series count:** reject deployments that introduce raw URLs, identities, IDs, SQL text, or
  exception strings as metric labels.
- **Gauge temporarily absent:** active-session and database-pool gauges omit a collection when the
  database is unavailable; dependency/readiness metrics continue to describe the outage.

The Portal API intentionally does not broaden the least-privileged runtime database role to read the
archive outbox. Its `portal.audit.outbox.backlog{scope="process"}` gauge is therefore the bounded
process-observed delta: successful audit writes increment it and successful archive notifications
decrement it. A future archive worker, running with the archive role, remains responsible for the
authoritative durable backlog across API restarts and replicas.
