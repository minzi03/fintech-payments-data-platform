# Central Control Plane

## Sources of truth

Phase 7 deliberately retains three state layers:

| State | Source of truth | Grain |
| --- | --- | --- |
| Airflow scheduling/execution | Airflow metadata tables | DAG/task instance |
| Cross-pipeline operational state | PostgreSQL `control` schema | pipeline run, aggregate quality, backfill request |
| Data-component lifecycle | Existing SQLite manifests | settlement file, CDC offset batch, Silver input object |

The `control` schema does not copy record-level rejections and does not replace component manifests.
It enables cross-pipeline operations without coupling existing reliability protocols to Airflow.

## Database and role boundary

`airflow-postgres` is independent from the payments OLTP database. `airflow-init` runs Airflow
migrations, bootstraps the versioned control DDL, and creates a non-superuser `pipeline_control`
login. DAG tasks use the environment-backed `control_db` Airflow connection; the metadata owner is
reserved for initialization/migrations.

The bootstrap is idempotent. SQL identifiers use safe composition and passwords are parameterized
as SQL literals. Neither password nor the authenticated URI is logged.

## Status mapping

Control pipeline/task statuses are `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `PARTIAL`, and
`SKIPPED`. Airflow retries update a deterministic task-attempt identity rather than inserting
duplicates. Pipeline identity is deterministic from `dag_id + airflow_run_id`.

## Limitations

- Component SQLite manifests remain single-host and are mounted into Airflow tasks.
- There is no distributed lease or cross-DAG asset lock yet.
- Control tables provide operational lineage, not an OpenLineage/catalog implementation.
- Retention, archival, alert delivery, and production RBAC are planned hardening work.
