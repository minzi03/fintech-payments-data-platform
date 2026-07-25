# ADR-003: Cross-System Recovery Checkpoints

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-003`

## Context

PostgreSQL logical replication, Kafka offsets, Bronze publication, Silver activation and control
metadata have independent durability boundaries. Restoring them independently can advance a
consumer beyond missing immutable data.

## Decision

Persist an append-only checkpoint for every committed Kafka next offset that references:

```text
source/slot identity
topic, partition, offset range and next offset
Bronze URI, checksum and object version
consumer group and manifest generation
verified timestamp
```

Active Silver activation records reference the exact Bronze inputs and Silver outputs. Startup
after restore runs a reconciliation command that verifies objects, compares Kafka committed
offsets, identifies the first missing coordinate, validates retention, and resets the group only
under an audited recovery operation.

## Alternatives considered

- Restore every system to approximately the same backup time: rejected because asynchronous
  backups do not establish a correctness boundary.
- Trust Kafka committed offsets: rejected because they do not prove object durability after an
  independent restore.

## Consequences

Checkpoint state is correctness-critical and requires HA storage. Recovery can intentionally stop
when Kafka retention no longer contains the required replay range.

## Migration plan

Backfill checkpoints from committed CDC manifests after verifying every referenced object. Records
without verifiable evidence remain non-authoritative and require a controlled baseline.

## Operational impact

Every recovery drill produces signed reconciliation evidence, RPO/RTO measurements and an explicit
go/no-go decision before consumers restart.

