# Workstream A Completion Report — F-003 Restore-Evidence Addendum

## Record Handling

The original Workstream A Completion Report is not a repository-backed file at the current
review target. This artifact is therefore an append-only evidence addendum; it does not
reconstruct, replace, or rewrite the historical report.

## Appended Evidence

F-003 restore-and-restart evidence was generated on `2026-07-27` against application commit:

```text
33376af2a39f50bdf8cdc792522ad2375f4b925c
```

The complete evidence record is:

```text
docs/governance/reviews/PORTAL-002-f003-restore-evidence.md
```

The controlled final execution:

- created and migrated an isolated source database;
- created a valid session, a pending login transaction, and a protected provider-token
  envelope through the Portal runtime;
- produced a custom-format PostgreSQL backup;
- restored the backup into an isolated database;
- restarted the Portal with a mandatory session-security-epoch bump and a bounded v2/v1 key
  transition;
- demonstrated authoritative invalidation of the restored epoch-1 session;
- completed the restored pending callback;
- decrypted the restored v1 envelope through the permitted previous key;
- wrote the successor session and envelope with v2;
- rejected unavailable, unsupported, and retired key versions;
- passed all 14 controlled checks.

Backup evidence:

```text
Artifact:
tmp/f003-restore-evidence/portal_f003_source_20260727t133146z.dump

SHA-256:
c79af88ab2fc361afcdfe5d6b40b46e5e835c911a0c2af2861e6e9c9ed2af156

Size:
48,508 bytes
```

Verification evidence:

```text
Restore drill:                  14 PASS, 0 FAIL
Focused unit tests:             28 passed
Focused integration tests:       3 passed
Ruff lint:                       PASS
Ruff formatting:                 PASS
Portal runtime mypy:             PASS (57 files)
Evidence harness mypy:           PASS (1 file)
```

## Acceptance-Criterion Update

```text
Restore behavior has an authoritative, testable result:
SATISFIED BY IMPLEMENTATION EVIDENCE
```

The result is governed as follows:

- normal process restart with the same epoch preserves the valid session;
- database restore increments the external session security epoch before startup;
- the restored old-epoch session returns `401`, transitions to `INVALID`, and records
  `SESSION_SECURITY_EPOCH_INVALID`;
- pending login and protected envelope state remain usable only under the explicitly
  configured, bounded key-transition policy.

## Remaining Gaps

No implementation-evidence gap remains for the F-003 restore criterion.

This addendum does not close F-003 and does not review-verify Workstream A.

## Updated Implementation Checkpoint

```text
F-001:
REVIEW-VERIFIED
CLOSED

F-003:
IMPLEMENTED
IMPLEMENTATION-VERIFIED
RESTORE EVIDENCE COMPLETED
OPEN
NOT REVIEW-VERIFIED

Workstream A:
Implementation: COMPLETED
Review: NOT ACCEPTED

Technical verdict:
REQUEST CHANGES
```

## Next Authorized Step

```text
Review Target Freeze (Evidence Update)
```

No technical re-review, merge, Milestone 2A authorization, or production authorization was
performed by this addendum.
