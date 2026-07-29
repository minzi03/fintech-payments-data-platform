# Portal production configuration model

S06-03 introduces a production-oriented configuration contract without enabling production
authentication or changing runtime security behavior.

## Boundaries

| Boundary | Contents | Lifecycle |
| --- | --- | --- |
| Flat compatibility loader | Existing `PORTAL_API_*` aliases and constructor overrides | Parsed once at process start |
| Role configuration | Frozen non-secret API, audit-worker or migration settings | Immutable for process lifetime |
| Secret references | Provider ID, logical identity, purpose and versions | Non-secret startup metadata |
| Environment secret inputs | Existing raw environment-backed secret values | Consumed only by the environment provider |
| Runtime state | Clients, engines, caches, tasks and provider handles | Process-local |
| Distributed ephemeral state | Redis abuse counters and bounded penalties | Reconstructible |
| Persisted authority | PostgreSQL sessions, replay fences, policy revisions and audit data | Durable |
| Deployment orchestration | Compose wiring, ports, volumes and infrastructure credentials | Outside application config |

## Dependency graph

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

The compatibility loader remains flat so existing Compose, CI and test inputs continue to work.
New composition roots call `for_role()` and retain only the domains required for that process.
Migration credentials use a migration-only input model and are not part of API or audit-worker
configuration.

## Profiles

The supported profiles are `local`, `test`, `development`, `staging` and `production`. A deployment
target is not an environment profile. Cloud-specific providers or orchestration remain future
adapters.

- Local and development warn on unknown `PORTAL_API_*` names to preserve compatibility while
  making drift visible.
- Test, staging and production reject unknown Portal-prefixed names deterministically.
- Staging and production require stronger transport/exposure settings.
- Production keeps the existing JSON logging, immutable build identity, HTTPS and OpenAPI guards.

Configuration errors and warnings expose safe field names and stable codes only. They never include
values, credential-bearing URLs or host-specific paths.

## Role ownership

### API

The API aggregate owns release identity, logging, HTTP exposure, health, telemetry, OIDC trust,
session/login policy, provider lifecycle, abuse configuration, key-rotation metadata and logical
secret references.

### Audit worker

The worker aggregate owns release identity, logging, telemetry, audit delivery/maintenance and its
database secret reference. It contains no HTTP, Web, OIDC or session-policy domain.

### Migration

The migration aggregate declares the PostgreSQL/psycopg dialect and forbids offline migrations.
The value of `PORTAL_MIGRATION_DATABASE_URL` stays in a dedicated secret input model.

## Portal Web

Portal Web uses separate typed loaders:

- public build configuration contains only `NEXT_PUBLIC_PORTAL_ENV`,
  `NEXT_PUBLIC_PORTAL_WEB_VERSION`, `NEXT_PUBLIC_PORTAL_BUILD_SHA` and the fixed relative API base;
- build/server configuration validates the CSP identity-provider origin without exposing it as
  browser runtime state;
- server runtime configuration owns the internal API URL, public Portal origin and host/port;
- test configuration owns Playwright URLs and activation flags.

Server-only URLs are not present in the serialized public configuration object.

## Source precedence and compatibility

Pydantic Settings retains the existing constructor-over-environment precedence and every historical
`PORTAL_API_*` field name. A canonical alias registry is checked against `.env.example`, Compose and
the configuration reference. No public configuration input is renamed.

Raw environment secrets are parsed as `SecretStr`, copied into `EnvironmentSecretInputs`, and used
only to construct `EnvironmentSecretProvider`. Selecting any other provider while inline
environment secret inputs are present fails closed rather than silently discarding them.

The `.env.example` Redis URL addresses the host-published development port for direct Python
execution. Compose deliberately replaces it with `redis://portal-redis:6379/0` inside the Portal API
container so host addresses do not leak into the container dependency graph.

## Decision log

| Decision | Rationale | Trade-off |
| --- | --- | --- |
| Freeze loader and domain models | Prevent post-start drift and make tests deterministic | Runtime reload is deferred |
| Keep flat compatibility facade | Avoid breaking 112 established aliases and constructor tests | Facade remains broader than new role views |
| Add role-scoped aggregates | Stop new composition boundaries from depending on unrelated domains | Existing leaf services migrate incrementally |
| Separate raw secret inputs | Preserve S06-02 provider boundary and safe diagnostics | Environment remains the local compatibility provider |
| Strict staging/production unknown-name rejection | Detect misspelling and drift before serving traffic | Local/development use warnings for compatibility |
| Keep production policy guards | S06-03 models configuration; it does not authorize security runtime | A valid production config is not an enabled production runtime |

## Deferred work and blockers

Production configuration contract implemented; production security runtime authorization remains
deferred.

The following guards remain unchanged and must not be interpreted as S06-03 gaps:

1. The Portal security runtime is not authorized for staging or production.
2. Callback policy remains local/test/development-only.
3. Abuse enforcement still uses development policy defaults.

Dynamic reload, secret hot rotation, configuration fingerprints, generated reference documentation,
container hardening, scanning, signing, provenance, release promotion, recovery and HA are outside
S06-03.

## Verification

The configuration suite covers model immutability, role isolation, environment secret separation,
provider conflicts, profile-specific unknown variables, staging transport rules, migration
isolation and alias drift. Portal Web tests cover build/runtime/test separation and malformed values.

Use:

```bash
make portal-config-check
make portal-test
docker compose --env-file .env.example config --quiet
```
