# Silver Processing Recovery Runbook

## Crash matrix

| Failure point | Manifest | Published meaning | Recovery |
| --- | --- | --- | --- |
| Before registration | None | None | Discover again |
| Read/validation failure | `FAILED` or `QUARANTINED` | No Silver publication | Repair source/version policy; force reprocess |
| Transform/serialization failure | `FAILED` | No Silver publication | Fix code/data policy; force a new run |
| First output written, later output fails | `FAILED` | Partial objects are not published | Preserve objects; rerun with new run ID |
| All outputs written, completion update fails | `WRITING` | Not published | Verify every checksum, then controlled repair or force new run |
| `COMPLETED` | Terminal | Outputs publishable | Normal discovery skips same identity |
| Same key, different checksum | No completion | Collision incident | Stop; never overwrite |

## Recovery procedure

1. Inspect the manifest without printing business payloads.
2. Verify input URI/checksum and source schema version.
3. For a partial write, list only `run_id=<failed-run>` objects and retain them as evidence.
4. Do not manually label a run completed unless every descriptor/object/checksum/count agrees.
5. Prefer `--force-reprocess` after correcting deterministic code/config; this produces a new run.
6. Confirm the new run is `COMPLETED`, then use only its referenced outputs downstream.

## Idempotency and current state

An existing input identity is skipped for the same code and Silver schema even when its prior run
failed; recovery therefore requires an explicit force flag. Force reprocess starts from the latest
completed `latest_all` snapshot whose input checksum differs from the replayed input, preventing a
run from treating its own prior output as upstream state. Earlier offsets from other inputs or a
business key on another partition generate quality evidence.

## Escalation

Escalate checksum collisions, advanced latest state with missing referenced object, incompatible
schema without a migration, or cross-partition key movement. Preserve manifest rows, object
metadata and hashes. Do not copy confidential Parquet payloads into logs or tickets.
