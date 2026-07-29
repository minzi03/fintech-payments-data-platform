# Canonical architecture and portfolio claims

## Authority

This document defines the approved boundary for claims made in the root README, architecture
documents, CV, LinkedIn, interviews, demos, and release notes. Evidence comes from the
[implemented architecture](current-state.md), tracked code/tests, and current checkpoint reports.

Classifications:

- **A — fully supported:** direct repository evidence supports the unqualified bounded claim.
- **B — supported with qualification:** evidence supports a named subsystem or boundary.
- **C — demonstrated locally only:** local tests/harnesses demonstrate behavior without production
  scale, topology, or SLO evidence.
- **D — designed but not production-authorized:** an architecture or guard exists, but deployment
  authority and production evidence do not.
- **E — unsupported:** do not use as an affirmative repository claim.

## Claim matrix

| Claim               | Class              | Evidence                                                              | Required qualifier                                                                 | Prohibited wording                               | Approved surfaces                               | Review trigger                         |
| ------------------- | ------------------ | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------- | -------------------------------------- |
| production-oriented | B                  | Typed configuration, security runtime, recovery controls, CI policy   | “local/reference implementation; production deployment and authorization deferred” | “production-grade enterprise platform”           | README, CV, LinkedIn, interview                 | Deployment or authorization changes    |
| production-grade    | E blanket claim    | No production topology/SLO/operations proof                           | May describe a specific technique as production-oriented                           | “the repository is production-grade”             | Technical discussion only, with subsystem scope | Production acceptance evidence         |
| production-ready    | E                  | Explicit production policy and deployment blockers remain             | State what remains deferred                                                        | “ready for production”                           | Limitation/roadmap only                         | FF-06 cannot promote this claim        |
| end-to-end          | B/C                | Executable source-to-Silver/Airflow paths                             | Name start/end and local evidence                                                  | “end-to-end enterprise analytics platform”       | README, demo, interview                         | Endpoint or runtime expansion          |
| real-time           | E                  | No production latency SLO                                             | Use near-real-time only for local CDC                                              | “real-time payments analytics”                   | Prohibited as affirmative claim                 | Measured production SLO                |
| near-real-time      | C                  | Local WAL/Debezium/Kafka CDC path                                     | “local CDC path; no production latency SLO”                                        | “production near-real-time SLA”                  | README, CV, demo, interview                     | Current latency benchmark/SLO          |
| exactly-once        | E                  | Cross-system transaction does not exist                               | State bounded effectively-once/idempotency instead                                 | “exactly-once pipeline”                          | Negative explanation only                       | New proven protocol boundary           |
| effectively-once    | B                  | Immutable conditional writes, deterministic identities, receipts      | Name immutable publication or delivery boundary                                    | “effectively-once across the platform”           | Architecture, demo, interview                   | Boundary/protocol changes              |
| idempotent          | B                  | Checksums, coordinates, manifests, receipts, bootstrap reconciliation | Name the operation and identity                                                    | “all operations are idempotent”                  | Architecture, CV, interview                     | Identity/state-machine changes         |
| fault-tolerant      | C                  | Local restart, replay, outage, poison, and recovery tests             | “bounded local failure/recovery demonstrations”                                    | “production fault-tolerant topology”             | Demo, interview                                 | HA/failover evidence                   |
| resilient           | B                  | Retries, fencing, fail-closed paths, recovery workflows               | Name subsystem and failure class                                                   | “universally resilient”                          | README, CV, interview                           | Recovery semantics change              |
| scalable            | C/D                | Partitioning and load harnesses; no production capacity proof         | “designed/tested for bounded concurrency locally”                                  | “production-scale”                               | Interview/design discussion                     | Current capacity/soak evidence         |
| highly available    | E                  | Single-node local dependencies                                        | Explicitly say HA is deferred                                                      | “highly available”                               | Limitation only                                 | Approved HA topology/failover          |
| secure              | B                  | OIDC, opaque sessions, encryption, abuse, audit, scanning             | “security-oriented with bounded residual risk”                                     | “fully secure”                                   | README, CV, interview                           | Threat/policy/finding changes          |
| hardened            | B                  | Non-root/read-only first-party Portal containers and runtime controls | Restrict to first-party Portal boundary                                            | “the complete platform is hardened”              | README, CV, security review                     | Image/runtime boundary change          |
| vulnerability-free  | E                  | Active governed findings remain                                       | Use “zero blocking findings under current policy”                                  | “no vulnerabilities”                             | Negative clarification only                     | Scan/policy/advisory change            |
| zero-trust          | E                  | No complete zero-trust architecture or proof                          | Describe specific trust boundaries                                                 | “zero-trust platform”                            | Prohibited as affirmative claim                 | Explicit architecture and verification |
| compliant           | E                  | No external control mapping or audit                                  | Describe technical controls, not certification                                     | “PCI/GDPR/SOC 2 compliant”                       | Prohibited as affirmative claim                 | Formal scoped assessment               |
| reproducible        | B                  | Hash locks, pinned inputs/actions/images, build manifest              | “immutable inputs and measured identity; OCI byte identity not guaranteed”         | “all builds are byte-identical”                  | README, CV, interview                           | Build-input/toolchain changes          |
| deterministic       | B                  | Seeded data, object identities, manifests, config errors              | Name selected input/identity/workflow                                              | “the entire distributed system is deterministic” | README, CV, architecture                        | Algorithm/identity changes             |
| immutable           | B                  | Bronze/Silver conditional publication and pinned artifacts            | Name object/artifact boundary                                                      | “all state is immutable”                         | README, CV, demo                                | Storage mutation semantics change      |
| observable          | B                  | Portal metrics/traces/logs and component evidence                     | “Portal/subsystem observability; not complete platform monitoring”                 | “fully observable platform”                      | README, CV, interview                           | Telemetry/alerting coverage change     |
| governed            | B                  | ADRs, policy, exceptions, audit, role boundaries                      | Name governance mechanism                                                          | “enterprise governance platform”                 | README, interview                               | Policy/authority changes               |
| self-healing        | E                  | Recovery requires bounded automation or operator action               | Describe individual recovery behavior                                              | “self-healing platform”                          | Prohibited as affirmative claim                 | Proven autonomous recovery             |
| DR-ready            | E                  | No approved backup/restore objectives or full drill                   | State DR is deferred                                                               | “DR-ready”                                       | Limitation only                                 | Recovery-objective evidence            |
| cloud-native        | E production claim | Compose/local services; no selected cloud/runtime contract            | May say containerized local/reference                                              | “cloud-native production platform”               | Prohibited as blanket claim                     | Approved deployment target             |
| deployment-ready    | C/D                | Compose and hardened images exist; production target absent           | “local Compose runnable; production deployment deferred”                           | “ready to deploy to production”                  | Demo, interview, limitation                     | Promotion/rollback and target evidence |

## Boundary-specific approved language

### CV

Approved:

> Built a production-oriented local payments data platform with PostgreSQL CDC, Kafka,
> immutable Bronze/Silver processing, Airflow orchestration, bounded replay semantics, and a
> separate hardened Portal security runtime.

Avoid:

> Built a production-grade enterprise platform with exactly-once processing and high availability.

### LinkedIn or repository summary

Approved:

> Local/reference implementation with source-to-Silver data engineering, explicit reliability
> boundaries, and deferred production deployment, HA/DR, and warehouse analytics.

Avoid language that implies a public production service, warehouse, dashboard, or compliance
certification.

### Interview

Approved:

> The project demonstrates source-to-Silver data engineering, bounded recovery semantics, and a
> separate hardened Portal runtime. PostgreSQL remains Portal security authority; Redis is
> reconstructible abuse state.

Do not say:

> The Portal is the unified control plane for the platform.

The Portal has no implemented Kafka, MinIO, Airflow, or Silver operational adapter.

### Demo

State the start and end of each path:

- settlement CSV through validation, Bronze/quarantine, and Silver evidence;
- PostgreSQL WAL through Debezium/Kafka, manual consumer, Bronze, and Silver;
- Portal login/session/abuse/audit behavior as a separate security-runtime demonstration.

Do not splice those demonstrations into a visual flow that implies Portal control over the data
plane.

### Release notes

Use verified commit/tag identities and distinguish:

- implemented behavior;
- locally demonstrated behavior;
- fixed or accepted security findings;
- deferred target direction.

Do not convert a passing local/security-policy gate into production authorization.

## Evidence requirements

A claim is reviewable only when it links to one or more of:

- tracked implementation and tests;
- immutable contract/schema;
- current runbook or architecture boundary;
- reproducible command and sanitized result;
- exact commit/tag/image identity;
- current security policy and bounded exception record.

Historical prompts, proposals, screenshots without provenance, and empty scaffold directories are
not implementation evidence.

The [canonical demo and evidence package](../demo/README.md) applies this matrix to live,
prepared, and offline presentation routes.

## Review triggers

Reassess affected rows when any of these changes:

- data path, state owner, schema, or public API;
- provider/session/abuse/audit authority;
- dependency lock, base image, scanner policy, or exception;
- deployment target, topology, identity, promotion, or rollback;
- latency, load, HA, restore, or production-acceptance evidence;
- warehouse, reconciliation, dashboard, or Portal adapter implementation.

## Related documents

- [Current implemented architecture](current-state.md)
- [Target and optional architecture](target-architecture.md)
- [Roadmap](../roadmap.md)
- [Security scanning](../portal/security-scanning.md)
- [Portal architecture boundary](../portal/architecture-boundaries.md)
