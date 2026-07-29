# Twenty-minute engineering deep dive

## Objective

Explain authority, reliability, security, and failure recovery in more detail while retaining the
same safe boundaries as the canonical demo.

## Required services and prepared state

The first twelve minutes work entirely from tracked evidence. Live services are optional for the
bounded CDC and Portal status segments. Prepared state uses the synthetic namespace documented in
the evidence package.

## Flow

| Time        | Topic                     | Evidence and discussion                                            |
| ----------- | ------------------------- | ------------------------------------------------------------------ |
| 00:00–02:00 | Business/source contracts | Business case, payments source grain, settlement contract          |
| 02:00–04:00 | Current architecture      | Data plane, control state, Portal, cross-cutting layers            |
| 04:00–06:00 | Batch semantics           | Checksum identity, partial acceptance, quarantine, quality reasons |
| 06:00–09:00 | CDC semantics             | WAL/pgoutput, Debezium envelope, coordinates, manual commit        |
| 09:00–11:00 | Bronze/Silver             | Immutable publication, history/latest/current, lineage             |
| 11:00–13:00 | Orchestration/state       | DAG dependencies and state-ownership matrix                        |
| 13:00–15:00 | Failure/recovery          | Replay, collision, poison, outbox, fencing, disposable validation  |
| 15:00–17:30 | Portal trust boundary     | OIDC, sessions, provider lifecycle, Redis, audit/outbox            |
| 17:30–18:30 | Security posture          | Five scanners, exact images, bounded dispositions                  |
| 18:30–20:00 | Trade-offs and limits     | Local scale, no HA/DR/warehouse/production authorization           |

## Optional live actions

Use only the one-transaction action and bounded inspectors defined in
[canonical-demo.md](canonical-demo.md). Do not add broader mutation merely because more time is
available.

## Deep-dive questions

### Why not exactly-once?

PostgreSQL, Kafka, MinIO, SQLite, Airflow, and Portal PostgreSQL have different authorities. The
implementation proves upload-before-offset-commit, deterministic identities, conditional writes,
and idempotent receipts at named boundaries; it does not create a global distributed transaction.

### Why component SQLite plus PostgreSQL control state?

Component manifests retain file/object/batch-grain evidence. PostgreSQL `control` retains
cross-pipeline run/quality/backfill state. Airflow metadata owns scheduling. Replacing all three
would require a separately designed authority migration.

### What happens when Redis fails?

Redis abuse state is reconstructible and has bounded operation-specific fallback. PostgreSQL still
owns sessions and durable replay; logout and crypto-erasure do not depend on Redis.

### Why is worker health degraded?

Database, destination, and outbox are `UP`; one intentional poison event is dead-lettered. This is
operationally available degraded evidence, not `DOWN`, and is intentionally retained.

## Fallback and cleanup

Every segment maps to a tracked asset in [fallback-demo.md](fallback-demo.md). Cleanup is no cleanup;
do not reset data, offsets, manifests, objects, volumes, migrations, or dead letters.

## Claim boundary

The extra duration authorizes explanation, not stronger claims. Use the same approved/prohibited
wording as [claims-script.md](claims-script.md).
