# Portal container hardening

S06-04 hardens the first-party Portal images and their local Compose runtime without changing
application behavior, public contracts, database schema, persistent authority, or security policy.

The implemented maturity boundary is:

> Container hardening implemented for first-party Portal services; production security policy
> authorization and security scanning remain deferred.

## Scope

The first-party hardening contract applies to:

- `portal-api`;
- `portal-audit-worker`;
- `portal-migrate`;
- `portal-web`.

PostgreSQL, Redis, and Keycloak remain vendor-owned images. Their version tags are retained for
human readability and pinned to immutable multi-architecture manifest digests. Vendor filesystem
reconstruction and arbitrary UID remapping are outside this task.

## Runtime identity and filesystem

Every first-party image declares `USER 10001:10001`. Application files are copied as
`root:root`, directories are normalized to `0755`, and regular files are normalized to `0644`.
The runtime identity can read and execute required content but cannot rewrite it.

Compose reinforces this boundary with:

```yaml
user: "10001:10001"
read_only: true
cap_drop:
  - ALL
security_opt:
  - no-new-privileges:true
```

The only first-party writable path is a bounded `/tmp` tmpfs:

| Service | Limit | Mount options |
|---|---:|---|
| API | 64 MiB | `rw,nosuid,nodev,noexec,mode=1777` |
| Audit worker | 64 MiB | `rw,nosuid,nodev,noexec,mode=1777` |
| Migration | 64 MiB | `rw,nosuid,nodev,noexec,mode=1777` |
| Web | 32 MiB | `rw,nosuid,nodev,noexec,mode=1777` |

No application state is persisted inside these containers. PostgreSQL remains the durable
authority and Redis remains reconstructible enforcement state.

## Image-content policy

First-party runtime images must have:

- fixed numeric non-root identity;
- root-owned, non-writable application content;
- no unexpected setuid or setgid binaries;
- no compiler toolchain;
- no Git metadata, `.env`, tests, reports, or package caches;
- the expected entrypoint, command, and exposed ports;
- no sensitive application variable names embedded in image metadata.

Python runtime images remove `pip`, setuptools, and wheel after installing verified locked wheels.
The Web runtime removes npm, npx, Corepack, and Yarn. Debian package database utilities and the
runtime shell are retained as documented exceptions.

### Source maps

Next.js currently emits nine empty section-map stubs. They are retained because deleting empty
metadata provides no material protection. CI parses every runtime source map and rejects:

- non-empty `sourceContent` or `sourcesContent`;
- non-empty source lists or mappings;
- absolute Unix, Windows, repository, or machine-specific paths;
- invalid JSON;
- credential/private-key markers.

## Network model

Exactly two Portal networks are used:

```text
Browser
   |
   v
Portal Web
   |
   | portal-app
   v
Portal API
   |
   | portal-data
   +----------+----------+
   |          |          |
PostgreSQL  Redis     Keycloak
   ^
   |
Worker / Migration
```

Membership:

| Service | `portal-app` | `portal-data` |
|---|---:|---:|
| Web | yes | no |
| API | yes | yes |
| Audit worker | no | yes |
| Migration | no | yes |
| PostgreSQL | no | yes |
| Redis | no | yes |
| Keycloak | no | yes |

Local host publications remain bound to `127.0.0.1`. These Compose networks reduce accidental
lateral reachability; they are not a replacement for production ingress or egress policy.

## Resource and lifecycle controls

Measured running-process counts were five PIDs for API, three for the audit worker, and eleven for
Web. The configured limits preserve a documented safety margin:

| Service | PID limit | Memory | CPU | Stop grace |
|---|---:|---:|---:|---:|
| API | 64 | 256 MiB | 0.50 | 30 s |
| Audit worker | 64 | 256 MiB | 0.50 | 45 s |
| Migration | 32 | 128 MiB | 0.25 | 30 s |
| Web | 64 | 384 MiB | 0.75 | 30 s |

Local `json-file` logs are bounded to three 10 MiB files per first-party container. Production
log-driver selection remains an operations decision.

The API continues to use Uvicorn directly as PID 1, the worker retains its explicit signal
handlers, and the Web uses the official Node entrypoint that executes the standalone server.
No defensive process supervisor is introduced.

Uvicorn and the worker exit cleanly through their installed shutdown paths. The standalone Node
server may report conventional exit code `143` when PID 1 terminates directly on SIGTERM; the
hardening gate accepts `0` or `143` only when shutdown completes inside the declared grace period.
It does not accept timeout or SIGKILL termination.

The migration job explicitly disables the API image healthcheck. Successful process completion,
not an HTTP readiness endpoint, is its lifecycle authority.

## Role-specific environment exposure

- API receives the configuration and secrets required by API composition.
- Worker receives only worker, archive-database, maintenance, telemetry, and provider-selection
  inputs.
- Migration receives only `PORTAL_MIGRATION_DATABASE_URL`.
- Web receives only its internal API URL and public origin; it receives no application secret.

Environment-provider compatibility remains from S06-02. Mounted secret files and concrete secret
managers are deferred.

## Verification

Static and runtime validation use the existing build path:

```powershell
python scripts/portal/verify_container_hardening.py
python scripts/portal/build_reproducible_artifacts.py `
  --output build/portal-artifacts/manifest.json
docker compose --env-file .env.example up -d --no-build --wait `
  portal-api portal-audit-worker portal-redis portal-web
python scripts/portal/verify_container_hardening.py --runtime
```

The runtime check verifies effective UID/GID, read-only filesystems, capability sets,
`NoNewPrivs`, image identity, network membership, positive dependency paths, and negative DNS
isolation without printing environment values.

CI also verifies health, public boundary behavior, and graceful first-party shutdown.

## Exception register

| ID | Service/image | Control | Reason | Risk | Compensating control | Verification | Owner | Review trigger |
|---|---|---|---|---|---|---|---|---|
| CH-001 | API/Web base images | Runtime shell retained | Official slim-image runtime and health/debug compatibility | Post-compromise command surface | Non-root, RO root, zero caps, NNP, segmented networks | Runtime-tool inventory | Portal platform | Base-image change |
| CH-002 | API/Web base images | `apt`/`dpkg` metadata retained | Removing core Debian package infrastructure is higher-risk than removing entrypoints | Post-compromise package discovery | RO root, zero capabilities, NNP, and segmented networks prevent package installation | Image inspection | Portal platform | Move to a new minimal base |
| CH-003 | PostgreSQL/Redis/Keycloak | Vendor roots remain writable | Vendor-specific write paths are not proven compatible with RO root | Larger vendor persistence surface | Immutable digest, isolated `portal-data`, loopback publication | Compose digest/network validation | Platform operations | Deployment contract approved |
| CH-004 | Keycloak | Development mode retained | Repository uses Keycloak as the local identity provider | Not a production identity topology | Loopback host publication and production runtime blockers | Compose and configuration tests | Identity platform | Production IdP decision |
| CH-005 | Portal Web | Empty source-map stubs retained | No sources or source content are present | Future build could emit source-bearing maps | CI parses all source maps and fails closed | Artifact inspection | Portal Web | Next.js upgrade |
| CH-006 | API/worker/migration | Environment secret adapter retained | S06-02 intentionally preserves environment compatibility | Docker-authorized operator can inspect values | Role-specific exposure, no image embedding, no value logging | Name-only runtime validation | Platform security | Concrete provider integration |
| CH-007 | Portal Web | SIGTERM may report exit `143` | Node is direct PID 1 and requires no forwarding wrapper | Exit code alone can be misread as ungraceful | Strict elapsed-time budget; no timeout or SIGKILL accepted | CI stop-grace check | Portal Web | Next.js runtime upgrade |

## Rollback

This hardening introduces no schema or state transformation. Rollback reverts the S06-04
Dockerfile, Compose, CI, verification, and documentation changes as one boundary.

- Never delete or recreate `portal_postgres_data`.
- Never clear the intentional dead letter.
- If RO-root execution exposes a required path, add only a bounded tmpfs for that path.
- If segmentation exposes a missing dependency, add only the minimum required network membership.
- If tool removal breaks runtime behavior, restore only the proven required tool and register the
  exception.

## Deferred work

S06-05 owns CVE, dependency, image-secret, license, malware/reputation, and base-image lifecycle
scanning. SBOM, signing, provenance, production network policy, KMS delivery, HA, backup/restore,
and deployment-platform controls remain outside S06-04.

Production blockers also remain explicit:

1. Security runtime is not authorized for staging or production.
2. Callback policy remains local/test/development-only.
3. Abuse runtime still uses development policy defaults.
4. No concrete non-environment secret provider is integrated.
