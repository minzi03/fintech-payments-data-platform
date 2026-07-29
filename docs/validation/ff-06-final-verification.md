# FF-06 final portfolio verification

## Decision

```text
Verification timestamp:
2026-07-29T16:58:18Z

Source branch:
feat/portal-002-runtime-conformance

Source commit before final commit:
2ab4f649deb5bd2c9c9e0718cac9c7dee096d571

Publication decision:
APPROVED LOCALLY

Push:
NOT PERFORMED

Merge:
NOT PERFORMED

Release:
NOT PERFORMED
```

FF-06 was rerun from phase 1 through phase 13 after FF-06A through FF-06E. No result from a
previous blocked run was used as approval evidence.

This approval is for public portfolio publication after a separately authorized push. It is not
production deployment authorization, a remote CI result, or a claim that the repository is
production-ready.

## Locked baseline

| Item                                  | Verified result                                            |
| ------------------------------------- | ---------------------------------------------------------- |
| Parent before final commit            | `2ab4f649deb5bd2c9c9e0718cac9c7dee096d571`                 |
| Parent's parent                       | `5a6fe89d7e0f010a640d2c9a68cfc366141348db`                 |
| Required baseline tag                 | `phase-portal-finalization-artifact-governance-child-sync` |
| Tracked tree before verification      | Clean                                                      |
| Staging before verification           | Empty                                                      |
| Protected untracked allowlist         | Exact nine-path set; unopened                              |
| Upstream relation before final commit | Ahead 23, behind 0                                         |
| PR #11                                | Open, pre-existing, unchanged                              |
| Push, merge, or release during FF-06  | None                                                       |

The complete annotated checkpoint chain was resolved through the Sprint 03–06 and FF-01–FF-06E
tags. The final verification tag is created only after this report and its manifest validate.

## Frozen artifact and scanner identities

| Evidence              | Identity                                                                  |
| --------------------- | ------------------------------------------------------------------------- |
| Portal API image      | `sha256:619d53a92abf74a6af53756dd412d1ece9cf23bc59b12a54fc691aa65630e8a3` |
| Portal Web image      | `sha256:1553e6a46cdffe8bb029ec4265e5a19fcf68fcc8e099051d66f16e42a83562f7` |
| Artifact manifest     | `sha256:27bea0e7d7e6df6f3167c441f6a0bf39fbeaae5d4ec40bea05c336afb8eabcdb` |
| Trivy version         | `0.70.0`                                                                  |
| Pinned Trivy database | `sha256:f6e81714e94ef9becd5e44f25d7b9888e5b7c257303888229d9c4709b30b1a3e` |
| Normalized findings   | `sha256:50db77dec1f4aabca73418f7f67eebdfc745ccadda6a881ca2769640553911d8` |
| Artifact report set   | `sha256:d391b8837f1faecc191d46a5c39521c516b1e188a146c8f7c86cb563e2a8f643` |
| Reachability evidence | `sha256:be66730a451bc7dd8083679a137ae4d76a46dfca2868c5e0ca0e0fe67b6c07bb` |
| Exception register    | `sha256:8129c2a8e5fb7c5c3f1cd7a1cb523b51c1c261df8226758770ee68216ec3ad44` |
| Central policy        | `sha256:d08d77daeef36ab1c9f73f976f096ab840e2651f25cf1b5b9c1689713f8e7140` |

The images were neither rebuilt, pulled, substituted, nor retagged. The pinned Trivy database was
used with update disabled and remained byte-identical throughout the run. OCI byte determinism
remains **not verified**.

## Artifact governance

Artifact ancestry, current-HEAD descendant validation, and the scanner invocation boundary passed.
The guard accepted exactly six explicit approved descendant paths introduced by FF-06C and FF-06D.
It accepted no directory, wildcard, prefix, artifact-affecting, scanner-policy, or exception-register
authorization. Mixed approved and unapproved descendants remained rejected. The exact-image scan
ran only after these checks passed.

## Verification matrix

| Gate                                                 | Fresh result                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------- |
| Baseline, tags, PR, and protected-path isolation     | PASS                                                                |
| Clean reviewer clone and 12-asset evidence inventory | PASS                                                                |
| Ruff                                                 | 321 files passed                                                    |
| Ruff formatting                                      | 321 files passed                                                    |
| mypy                                                 | 90 Portal source files passed                                       |
| OpenAPI                                              | `9fab4ac5b9e41dab87f648d2e797a99caefe51fefacacf4407df97f694471fb2`  |
| Alembic                                              | Single head `008_audit_outbox_and_maintenance`                      |
| JSON/YAML and Compose                                | PASS                                                                |
| Tracked Markdown links/anchors                       | 194 local links passed                                              |
| Tracked Mermaid blocks                               | 15 passed                                                           |
| Source security scan                                 | 16 findings, 0 blocking                                             |
| Git-history security scan                            | 0 findings, 0 blocking                                              |
| Security/governance tests                            | 100 passed                                                          |
| Portal backend                                       | 274 passed, 41 skipped, 6 destructive deselected                    |
| Disposable Portal database integration               | 56 passed                                                           |
| Disposable migration validation                      | 6 passed                                                            |
| Redis integration                                    | 4 passed                                                            |
| Root/platform regression                             | 360 passed, 2 skipped, 34 deselected                                |
| Disposable MinIO/Silver                              | 9 passed; zero residual run-owned resources                         |
| CDC integration                                      | 5 passed, 2 environment-constrained Airflow skips                   |
| Batch integration                                    | 8 passed                                                            |
| Airflow/unit                                         | 27 passed, 2 environment-constrained skips                          |
| Foundation                                           | 7 passed                                                            |
| Frontend format/lint/typecheck/build                 | PASS                                                                |
| Vitest                                               | 29 passed                                                           |
| Playwright                                           | 1 foundation test passed, 5 auth-specific tests skipped as designed |
| Exact-image scan                                     | 1,340 findings, 0 blocking                                          |
| First-party Critical/High                            | 45, all no-fix under bounded disposition                            |
| Fixed first-party Critical/High                      | 0                                                                   |
| Security exceptions                                  | 46 active, 0 expired, 0 within 30 days                              |
| Earliest exception expiry                            | 2026-09-27                                                          |
| Container hardening                                  | PASS for Compose contract and both frozen images                    |
| Live CDC rehearsal                                   | PASS                                                                |
| Offline fallback                                     | 12 assets, 5 evidence categories, no runtime dependency             |
| Documentation and claim audit                        | PASS                                                                |
| Public-review simulation                             | PASS with documented limitations                                    |
| Intentional dead letter                              | Unchanged and expected                                              |

The repository-wide Markdown Prettier probe found legacy formatting variance outside the mandatory
tracked build gates. No file was reformatted for that informational result. The canonical frontend
format gate and all changed-file formatting checks passed.

## Live CDC trace

The one-use synthetic seed `50501` created one customer, account, merchant, payment transaction,
and lifecycle event in one source transaction. The exact payment transaction identity was
`76e41690-1da1-5e9b-8def-d7a2cc5e3dec`.

Fresh evidence showed:

```text
PostgreSQL source commit
  -> Debezium operation c, snapshot false
  -> Kafka partition 1, offset 10
  -> run-scoped consumer, one event
  -> immutable Bronze batch e2f9765d2f487eeedc58fb88ca604f00d2ba707832cade57def213bd3f5deb86
  -> Silver run 68c2aacf-f2b9-4f0e-b7a9-48e01f8cd918
  -> COMPLETED, 0 rejected records
```

The immutable synthetic source, Bronze, and Silver evidence is intentionally retained. No offsets,
manifests, objects, volumes, or existing records were reset or deleted.

## Offline and degraded-mode evidence

The offline route was rehearsed from the clean clone. All 12 manifest assets matched their SHA-256
identities and covered architecture, data-platform, Portal, security, and validation evidence with
zero runtime services and zero persistent impact.

The live Airflow view was unavailable, so the documented tracked DAG summary was used. This is an
expected fallback, not evidence of a live Airflow run.

Portal audit-worker health remained:

```text
overall: DEGRADED
database: UP
destination: UP
outbox: UP
pending: 0
dead_lettered: 1
maintenance_overdue: 0
```

The single dead-letter record is intentional validation evidence and was not modified.

## Public claims and limitations

Approved wording remains bounded to a production-oriented local/reference implementation.
Source-to-Silver, selected effectively-once publication boundaries, a separate hardened Portal
runtime, and local failure/recovery demonstrations are evidence-backed.

The following remain explicitly unsupported as blanket claims:

- production-ready or production-grade platform;
- exactly-once end to end;
- vulnerability-free;
- highly available or DR-ready;
- production latency or scale guarantees;
- warehouse, dbt, Gold, dashboard, or reconciliation runtime;
- Portal control over Kafka, MinIO, Airflow, or Silver;
- byte-identical OCI reproducibility;
- production security-runtime authorization.

## Publication boundary

All mandatory FF-06 gates passed. Portfolio publication is therefore approved locally. A push,
merge, pull-request mutation, GitHub release, repository-visibility change, or production rollout
still requires separate authorization.

The machine-readable checkpoint is
[`ff-06-final-manifest.json`](ff-06-final-manifest.json).
