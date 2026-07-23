# Backfill Runbook

Backfill is manual, one active run at a time, and accepts only a UUID request ID, `CDC` or
`SETTLEMENT`, an allowlisted CDC entity, relative object prefix, ISO date range, force flag, and
dry-run flag. Parameters cannot execute shell commands.

Start with dry-run:

```bash
make trigger-backfill BACKFILL_CONF='{"request_id":"<uuid>","source_type":"CDC","entity":"customers","from_date":"2026-07-01","to_date":"2026-07-22","dry_run":true}'
```

Dry-run validates/reads through the existing processor but writes no Silver objects, component
manifest state, or control request. Review the task result, then use a new request UUID with
`dry_run=false`. `force_reprocess=true` creates a new immutable Phase 6 run/object lineage; it never
overwrites prior Silver.

Do not overlap forced backfills with the same entity/date normal schedule. Pause the normal DAG or
wait for it to complete, then run at bounded concurrency. Never reset Bronze or Kafka as part of a
Silver backfill.
