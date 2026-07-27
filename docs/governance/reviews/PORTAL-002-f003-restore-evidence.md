# F-003 Restore-Evidence Report

## Status

```text
Execution:                 COMPLETED
Scope:                     Local/development only
Finding:                   F-003
Implementation changes:    NONE
Evidence gap remaining:    NONE for restore-and-restart execution
Finding closure:           NOT PERFORMED
Review verification:       NOT PERFORMED
```

This report records the controlled database backup, restore, and Portal restart exercise
requested after the Workstream A targeted re-review. It supplies execution evidence for the
remaining F-003 criterion:

> Restore behavior has an authoritative, testable result.

The governed restore behavior differs from an ordinary process restart:

- an ordinary restart with the same security epoch preserves a valid session;
- a database restore requires the external session security epoch to advance before startup;
- a session restored with the previous epoch must fail closed and must not become trusted
  automatically.

The drill therefore treats authoritative restored-session invalidation, not restored-session
reuse, as the required PASS result.

## Execution Scope

The final authoritative run:

- created isolated source and restore databases;
- migrated the source database to the governed schema head;
- created a valid session, pending login transaction, and protected provider-token envelope
  through the Portal runtime;
- stopped the source runtime before backup;
- backed up the source with `pg_dump`;
- restored it with `pg_restore`;
- restarted the Portal against the restored database with security epoch `2`, current key
  `f003-restore-v2`, and previous key `f003-restore-v1`;
- verified restored state, transition behavior, and fail-closed outcomes;
- left the normal `portal_control` database unchanged.

No runtime implementation file was changed.

## Environment

| Field | Value |
| --- | --- |
| Operating system | Windows 11 `10.0.26100` |
| Python | `3.14.6` |
| PostgreSQL | `16.4 (Debian 16.4-1.pgdg120+2)` |
| PostgreSQL timezone | `Etc/UTC` |
| Application commit | `33376af2a39f50bdf8cdc792522ad2375f4b925c` |
| Application tree | `16e967c1ae85babaa4474b8e25b0d85a738d36e5` |
| Schema head before restore | `006_session_revocation_fence` |
| Schema head after restore | `006_session_revocation_fence` |
| Recorded schema migrations | `6` |
| Source database | `portal_f003_source_20260727t133146z` |
| Restored database | `portal_f003_restored_20260727t133146z` |
| Source security epoch | `1` |
| Restored runtime security epoch | `2` |
| Source key version | `f003-restore-v1` |
| Restored current key version | `f003-restore-v2` |
| Restored previous key version | `f003-restore-v1` |
| Transition start | `2026-07-27T13:30:48.621113Z` |
| Transition expiry | `2026-07-27T13:41:48.621121Z` |

The master-key values and browser/session secrets were not emitted into this artifact.

## Backup Evidence

| Field | Value |
| --- | --- |
| Backup started | `2026-07-27T13:31:47.412110Z` |
| Backup/restore completed | `2026-07-27T13:31:48.586254Z` |
| Local artifact | `tmp/f003-restore-evidence/portal_f003_source_20260727t133146z.dump` |
| Size | `48,508` bytes |
| SHA-256 | `c79af88ab2fc361afcdfe5d6b40b46e5e835c911a0c2af2861e6e9c9ed2af156` |
| Preservation | Retained locally; no cleanup performed |

## Command Log

All timestamps are UTC.

| Timestamp | Phase | Command or action | Result |
| --- | --- | --- | --- |
| `2026-07-27T13:31:46.813676Z` | Database preparation | `CREATE DATABASE portal_f003_source_20260727t133146z OWNER portal_migration; GRANT CONNECT ...` | Completed |
| `2026-07-27T13:31:46.873093Z` | Database preparation | `CREATE DATABASE portal_f003_restored_20260727t133146z OWNER portal_migration; GRANT CONNECT ...` | Completed |
| `2026-07-27T13:31:47.187661Z` | Migration | `alembic -c apps/portal-api/alembic.ini upgrade head` against the isolated source | Completed |
| `2026-07-27T13:31:47.728257Z` | Backup | `docker compose exec -T portal-postgres pg_dump --username=portal_admin --format=custom --dbname=portal_f003_source_20260727t133146z --file=/tmp/portal_f003_source_20260727t133146z.dump` | Exit `0` |
| `2026-07-27T13:31:47.928578Z` | Preservation | `docker compose cp portal-postgres:/tmp/portal_f003_source_20260727t133146z.dump tmp/f003-restore-evidence/portal_f003_source_20260727t133146z.dump` | Exit `0` |
| `2026-07-27T13:31:48.586239Z` | Restore | `docker compose exec -T portal-postgres pg_restore --username=portal_admin --no-owner --exit-on-error --dbname=portal_f003_restored_20260727t133146z /tmp/portal_f003_source_20260727t133146z.dump` | Exit `0` |
| `2026-07-27T13:31:48.621121Z` | Restart | Portal application instantiated against the restored database using the durable transition configuration and epoch `2` | Completed |
| `2026-07-27T13:31:48.801716Z` | Verification | Final evidence evaluation | Completed |

The complete, repeatable command is:

```powershell
python scripts/portal/f003_restore_evidence.py
```

The harness uses isolated database identifiers, redacts secret values from output, and retains
the databases and dump instead of deleting evidence.

## Database State Summary

The restored database held the following authoritative state after restart and verification:

```text
portal_sessions
ACTIVE  | epoch=2 | lookup_key_version=f003-restore-v2 | count=1
INVALID | epoch=1 | lookup_key_version=f003-restore-v1 | count=1

oidc_login_transactions
CONSUMED | browser_binding_key_version=f003-restore-v1 | count=2

portal_token_envelopes
f003-restore-v1 | count=1
f003-restore-v2 | count=1
```

The restored runtime retained `USAGE` on `portal_control` and
`SELECT, INSERT, UPDATE` on `portal_control.portal_sessions`.

## Key-Version Summary

- The source session, pending login, and provider envelope were written with
  `f003-restore-v1`.
- The restored runtime started with `f003-restore-v2` as current and
  `f003-restore-v1` as previous inside a bounded transition window.
- The pending login's v1-protected verifier remained decryptable inside the window.
- A new post-restore session was written with v2 and security epoch `2`.
- The restored v1 provider envelope decrypted only while v1 was a permitted previous key.
- Removing v1, presenting an unsupported version, or expiring the transition window caused
  deterministic fail-closed results.

## Functional Results

| Criterion | Result | Evidence |
| --- | --- | --- |
| Controlled database backup | PASS | `pg_dump` exit `0`; retained dump has recorded size and SHA-256 |
| Controlled database restore | PASS | `pg_restore --exit-on-error` exit `0` into the isolated restored database |
| Correct schema and privilege state after restore | PASS | Schema remained at `006`; six governed migrations and runtime grants were present |
| Session exists and is usable before backup | PASS | `/v1/session` returned HTTP `200`; row was `ACTIVE`, epoch `1`, key v1 |
| Restored session has an authoritative result | PASS | After mandatory epoch bump, `/v1/session` returned `401`; row became `INVALID`; audit reason was `SESSION_SECURITY_EPOCH_INVALID` |
| Restored pending-login transaction remains processable | PASS | Callback returned HTTP `303`; restored transaction became `CONSUMED` |
| Restored protected envelope remains decryptable | PASS | v1 envelope decrypted through the recorded previous key; expected access/refresh fields were recovered |
| Correct current-key selection after restore | PASS | New session was `ACTIVE`, epoch `2`, lookup key v2 |
| Unavailable key version fails closed | PASS | v1 envelope was rejected when only v2 was available |
| Unsupported key version fails closed | PASS | `f003-unsupported-v999` was rejected |
| Retired key version fails closed | PASS | v1 envelope was rejected after deterministic transition expiry |
| Migration compatibility | PASS | Source and restored databases reported the same governed head |

## Verification Results

### Evidence drill

```text
python scripts/portal/f003_restore_evidence.py
Result: COMPLETED
Functional checks: 14 PASS, 0 FAIL
Remaining gaps: none
```

### Unit tests

```text
python -m pytest \
  apps/portal-api/tests/unit/test_protected_value.py \
  apps/portal-api/tests/unit/test_security_material.py \
  apps/portal-api/tests/unit/test_config.py \
  -q -p no:cacheprovider

Result: 28 passed
```

### Restart, transition, and security-negative integration tests

The tests ran against isolated database
`portal_f003_regression_20260727t133000z`.

```text
python -m pytest \
  apps/portal-api/tests/integration/test_callback_orchestration.py \
  -q -p no:cacheprovider \
  -k "session_and_pending_callback_survive_process_restart or \
      restart_during_and_after_controlled_key_transition_is_deterministic or \
      unknown_session_key_version_fails_closed"

Result: 3 passed, 14 deselected
```

The controlled transition integration test includes rollback acceptance inside the active
transition window and expiry rejection after the window.

### Lint and formatting

```text
python -m ruff check --no-cache \
  apps/portal-api/app apps/portal-api/tests \
  scripts/portal/f003_restore_evidence.py

Result: All checks passed

python -m ruff format --check --no-cache \
  apps/portal-api/app apps/portal-api/tests \
  scripts/portal/f003_restore_evidence.py

Result: 76 files already formatted
```

### Static typing

```text
python -m mypy --no-incremental \
  --cache-dir <local-temporary-cache> \
  --config-file apps/portal-api/pyproject.toml \
  apps/portal-api/app/portal_api

Result: Success; no issues found in 57 source files

python -m mypy --no-incremental \
  --cache-dir <local-temporary-cache> \
  --strict --follow-imports=skip \
  scripts/portal/f003_restore_evidence.py

Result: Success; no issues found in 1 source file
```

The temporary mypy cache was used because the repository cache directory is not writable in
the local execution sandbox. That constraint did not affect type-check coverage.

One non-blocking `StarletteDeprecationWarning` was emitted by the installed test-client
dependency. No test failed.

## Harness Validation Attempts

Three earlier isolated executions stopped because of evidence-harness query defects. They did
not establish the authoritative result and did not change Portal runtime implementation:

| Run | Stop reason |
| --- | --- |
| `20260727t132620z` | Post-restore evidence query used the migration role across a least-privilege schema boundary |
| `20260727t132700z` | Audit evidence query referenced a nonexistent `session_id` column instead of `session_reference` |
| `20260727t132730z` | Audit evidence query used the enum label instead of the stored versioned event identifier |

Run `20260727t132755z` completed successfully while the harness was being finalized. The
authoritative report is based on the subsequent linted and type-checked run
`20260727t133146z`.

All isolated databases and dump artifacts remain local. No cleanup was performed so the
execution history is not silently erased.

## Repository Impact

The remediation added only:

- `scripts/portal/f003_restore_evidence.py` — repeatable local/development evidence harness;
- this restore-evidence report;
- an append-only Workstream A completion-report addendum.

No file under `apps/portal-api/app/portal_api/` was modified.

The dump artifacts are under the ignored `tmp/` directory and are not proposed as source
repository content.

## Remaining Gaps

No F-003 restore-and-restart execution criterion remains unsupported by this remediation
evidence.

This statement is an implementation/evidence result only. F-003 remains `OPEN` and
`NOT REVIEW-VERIFIED` until a separately authorized independent targeted re-review evaluates
the new immutable evidence target.

## Completion Status

```text
F-003 Restore-Evidence Remediation:  COMPLETED
Workstream A implementation:         COMPLETED
Workstream A review:                 NOT ACCEPTED (unchanged)
F-003 finding:                       OPEN (unchanged)
F-003 review verification:           NOT REVIEW-VERIFIED (unchanged)
```

## Next Authorized Step

```text
Review Target Freeze (Evidence Update)
```

This report does not authorize technical re-review, merge, Milestone 2A, or production.
