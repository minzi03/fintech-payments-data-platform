# ADR-002: CDC Key-Only Delete Semantics

- Status: Proposed
- Date: 2026-07-24
- Backlog: `PRD-002`

## Context

PostgreSQL default replica identity can emit a delete pre-image containing only key columns. Silver
currently projects delete `before` data as though it were a complete entity row.

## Decision

Delete is a key-addressed mutation:

```text
delete key
→ resolve prior active/candidate state
→ preserve prior business attributes
→ apply delete coordinates and deleted flag
→ append immutable delete history
→ exclude the entity from CURRENT
```

If prior state is absent, the event is not silently converted into a partial state row. It is
quarantined as `DELETE_PRIOR_STATE_MISSING` unless that table has an approved full-pre-image
contract. `REPLICA IDENTITY FULL` is enabled selectively only when audit requirements justify its
WAL and storage cost.

## Alternatives considered

- Require `REPLICA IDENTITY FULL` on every table: rejected because it couples correctness to higher
  WAL volume and still does not remove the need for robust key-only semantics.
- Trust tombstones to remove CURRENT: rejected because tombstones are transport cleanup records,
  not the business delete contract.

## Consequences

Silver state lookup becomes required for deletes. Delete history records the source key and
coordinates even when the source pre-image is key-only. Missing state becomes an explicit recovery
case.

## Migration plan

Add the delete quality code and merge path, validate real Debezium events under default replica
identity, then repair or redrive historical rejected deletes.

## Operational impact

Alert on missing-prior deletes and monitor any table configured with full replica identity for WAL
growth.

