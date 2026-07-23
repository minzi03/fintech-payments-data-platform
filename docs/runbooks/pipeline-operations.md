# Pipeline Operations Runbook

## Normal operations

```bash
make airflow-dags-list
make trigger-settlement-pipeline
make trigger-cdc-silver-pipeline
make airflow-logs
```

The settlement DAG runs file discovery, Phase 2/3 ingestion, manifest validation, Phase 6
settlement processing, and an aggregate rejection-rate gate. It does not reconcile settlements.

The CDC health DAG assumes the profile-gated CDC consumer is a separate continuously running
service. It checks logical WAL, required topics, connector/task state, committed group lag, and
manifest freshness. Fail thresholds block success; warn thresholds produce `PARTIAL`.

The CDC Silver DAG processes entities in reference order. Existing Silver manifests skip completed
inputs, so Airflow retry does not create new outputs. XCom must stay below operational metadata size;
inspect object bodies with existing storage tools, not the Airflow UI.

## Target SLAs

- Settlement discovery to Bronze: under 15 minutes.
- Settlement Silver: before 07:00 UTC or configured business deadline.
- CDC connector freshness: under 2 minutes target.
- CDC group lag: below configured warning threshold.
- CDC Silver freshness: under 15 minutes target.

These are targets. Phase 7 records timestamps/counts but makes no benchmark or achieved-SLA claim.

## Quality response

For `WARN`, review `control.data_quality_results` and component rejections; successful artifacts
remain valid and the run is `PARTIAL`. For `FAIL`, resolve the source/component issue, then clear by
retry or an explicit backfill. Do not edit immutable objects or manifest rows manually.
