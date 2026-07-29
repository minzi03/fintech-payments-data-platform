# Fail-closed demo preflight

## Identity and repository

- [ ] Branch is `feat/portal-002-runtime-conformance`.
- [ ] Source checkpoint/tag matches the evidence manifest.
- [ ] Tracked tree is clean.
- [ ] Staging is empty.
- [ ] The exact nine-path user-owned allowlist is unchanged and remains unopened.
- [ ] No push/release state is implied by local commits.

Any mismatch disables live actions. Continue with the offline fallback.

## Safety

- [ ] No `.env`, credentials, tokens, cookies, emails, raw payloads, or audit payloads are visible.
- [ ] Browser has only presentation tabs; profiles, bookmarks, notifications, and unrelated tabs
      are hidden.
- [ ] No terminal contains local absolute paths, usernames, history with secrets, or internal URLs.
- [ ] No reset, truncate, downgrade, cleanup, volume deletion, or dead-letter repair is planned.
- [ ] Fallback assets and claims script are open.

## Prepared data

- [ ] Demo seed/namespace is recorded as `50501`.
- [ ] The one-use live seed is proven absent before generator execution.
- [ ] Existing synthetic evidence is preferred when the seed already exists.
- [ ] Batch evidence uses the tracked contract/summary; no broad reprocessing is needed.
- [ ] Expected outputs and no-cleanup policy are understood.

## Live dependency checks

- [ ] PostgreSQL source is healthy.
- [ ] Debezium connector/task and Kafka are healthy.
- [ ] MinIO is healthy and bounded inspectors work.
- [ ] Airflow scheduler/metadata dependencies are healthy if a live DAG view is planned.
- [ ] Portal PostgreSQL and OIDC are healthy if a live Portal walkthrough is planned.
- [ ] Redis status/fallback is understood.
- [ ] Audit worker health matches `DEGRADED`, dependencies `UP`, pending `0`, dead-lettered `1`,
      maintenance overdue `0`.

Unexpected state disables the affected live segment. Do not repair it during preflight.

## Duration and fallback

- [ ] Audience route is selected: 5, 10, or 20 minutes.
- [ ] Every live step has an offline asset.
- [ ] Bounded command stop conditions are understood.
- [ ] Slow startup or delayed CDC/Silver switches to fallback rather than extending the demo.
- [ ] Cleanup remains no cleanup.

## Go/no-go

Live demo is `GO` only when every relevant item is checked. Otherwise:

```text
LIVE DEMO: NO-GO
OFFLINE FALLBACK: USE TRACKED PACKAGE
```

This checklist never authorizes destructive commands or production claims.
