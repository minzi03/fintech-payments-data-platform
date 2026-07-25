# ADR-004: Runtime Identity and Storage IAM

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-004`

## Context

Bootstrap, migration, ingestion, transformation, orchestration and recovery require materially
different privileges. Reusing owner/root credentials turns a service compromise into a platform
compromise.

## Decision

Define separate identities for:

- database migration ownership;
- OLTP application writes;
- Debezium replication and captured-table reads;
- CDC Bronze writes;
- Silver Bronze reads and Silver writes;
- Airflow control-plane writes;
- read-only operations;
- audited recovery administration.

Object-store policies deny overwrite and delete to runtime identities. Secrets come from the
deployment secret manager and rotate independently. Production configuration validation rejects
root credentials and public endpoint exposure.

## Alternatives considered

- One identity per environment: rejected because it provides no blast-radius boundary.
- Application-enforced restrictions only: rejected because a compromised process can bypass them.

## Consequences

Provisioning becomes more complex and permission changes require tested migrations. Incidents gain
clearer attribution and bounded blast radius.

## Migration plan

Inventory current operations, introduce read-only identities first, split writers by storage
namespace, rotate services one at a time, and remove legacy owner credentials after denial tests.

## Operational impact

Credential expiry, policy denials and rotation status become monitored signals. Recovery access is
break-glass and audited.

