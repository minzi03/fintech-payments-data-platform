# Orchestration Control Schema

Schema: `control` in the dedicated Airflow PostgreSQL database.

## `pipeline_runs`

Grain: one Airflow pipeline run. Unique `(dag_id, airflow_run_id)`; deterministic UUID primary key.
It stores logical/run times, run type, aggregate counts, input/output asset references, terminal
status, and sanitized failure codes/messages. JSON assets are URI/ID lists, never record payloads.

## `task_runs`

Grain: one task try within a pipeline run. Unique `(pipeline_run_id, task_id, try_number)`.
Retry updates are idempotent. `result_metadata` is limited to counts/classification identifiers.

## `data_quality_results`

Grain: one named aggregate rule per pipeline run. Stores `PASS/WARN/FAIL`, observed numeric value,
thresholds, and small details. Record-level rejection evidence remains in Silver/quarantine.

## `backfill_requests`

Grain: one operator-supplied UUID request. It stores allowlisted source/entity/prefix/date controls,
force/dry-run flags, Airflow run linkage, and lifecycle status. Date ranges are constrained.

## `asset_watermarks`

Grain: latest watermark per pipeline and asset. Added for measured freshness, but Phase 7 health
checks still read existing manifests directly; this table is not a replacement ledger.

All timestamps are `TIMESTAMPTZ`; counters are non-negative; status/type values have CHECK
constraints. DDL is versioned in `infrastructure/airflow/init/001_create_control_schema.sql`.
