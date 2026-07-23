# Orchestration Recovery

| Failure point | Safe response |
| --- | --- |
| Settlement file quarantined | Correct upstream file under a new content checksum/name; re-ingest. Do not edit quarantine. |
| Bronze uploaded, Airflow task failed | Retry; the immutable write and settlement manifest make it idempotent. |
| Silver object written, task failed | Retry; only a completed Phase 6 manifest publishes the run. Orphan attempt objects remain non-publishable. |
| Component manifest completed, Airflow failed | Retry control task; component returns skipped/completed identity without duplicate output. |
| Scheduler/API restart | Named metadata/log volumes retain state; restart services and let Airflow resume retries. |
| Kafka Connect unhealthy | Inspect connector status/logs, restore `RUNNING`, then rerun the bounded health DAG. |
| CDC consumer lag high | Check consumer service/manifest and storage availability; Airflow must not start an infinite replacement task. |
| Processing run stuck | Inspect the component manifest and object lineage; use explicit recovery/backfill, not SQL status mutation. |
| Partial Silver output | Leave immutable attempt objects; retry/reprocess creates or confirms a complete published run. |

Failure callbacks log only DAG/task/run identifiers and exception type. Secrets, connection URIs,
payloads, and customer data are redacted. Central control failure text is truncated and must never
contain raw records.
