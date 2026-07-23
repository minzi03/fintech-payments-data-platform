# Airflow Orchestration Architecture

## Status and boundary

Implemented in Phase 7 with Apache Airflow 3.3.0, Python 3.11, `LocalExecutor`, a dedicated
PostgreSQL metadata database, and a separate `control` schema. Airflow coordinates the Phase 2–6
applications; it does not contain settlement validation, CDC parsing, or Silver transformations.
Snowflake, dbt execution, Gold marts, Spark/Flink, dashboards, and a full observability platform
remain out of scope.

Airflow 3 calls the UI/API process `api-server`; the Compose service retains the familiar local name
`airflow-webserver`. A separate `dag-processor` is required by Airflow 3. The triggerer is omitted
because these DAGs use no deferrable operators.

## DAGs

| DAG | Schedule | Purpose |
| --- | --- | --- |
| `settlement_batch_pipeline` | `AIRFLOW_SETTLEMENT_SCHEDULE` | Inbound discovery, immutable Bronze ingestion, Settlement Silver, aggregate quality gate. |
| `cdc_bronze_control` | `AIRFLOW_CDC_HEALTH_SCHEDULE` | WAL, Kafka, Connect, connector, group-lag, and CDC manifest-freshness checks. |
| `cdc_silver_processing_pipeline` | `AIRFLOW_SILVER_SCHEDULE` | Entity-ordered Phase 6 processing and aggregate Silver quality gate. |
| `data_platform_backfill` | manual only | Validated, concurrency-limited CDC or settlement reprocessing/dry-run. |

All DAGs use a fixed timezone-aware start date, explicit `catchup=False`, bounded retries/timeouts,
one active run, and a redacted failure callback. The default timezone is UTC. Business-local
settlement deadlines are targets documented in the runbook, not benchmark claims.

## CDC operating model

Phase 7 uses model A: the CDC consumer runs continuously outside Airflow using its Compose profile.
Airflow only performs bounded health/control checks. It never holds an infinite polling task and
does not replace Kafka consumer-group coordination.

## Dependency and XCom policy

The CDC Silver DAG preserves reference order: customers before accounts; customers, accounts, and
merchants before payments; payments before refunds and transaction events. Temporarily unresolved
references retain Phase 6 semantics.

XCom is restricted to run IDs, object URIs, paths, checksums, counts, status, and compact metadata.
CSV rows, CDC envelopes, Parquet bytes, customer fields, and rejection payloads never use XCom.

## Retry and partial success

Retries call the existing idempotent components. Settlement checksum identity, immutable storage,
CDC offset-range identity, and Silver input identity prevent retry overwrite. Aggregate quality is:

- `PASS`: orchestration run becomes `SUCCEEDED`;
- `WARN`: run becomes `PARTIAL` and publication metadata remains available;
- `FAIL`: run becomes `FAILED` and downstream success publication is blocked.

One quarantined settlement file can coexist with successful files. A run fails hard only if no file
reaches Bronze or a fail-level gate is crossed.
