# Local Kafka and Debezium Runbook

## Scope

Operate the Phase 4 single-node Kafka KRaft broker, Debezium Kafka Connect worker, and PostgreSQL CDC
connector. The flow ends at Kafka topics. This runbook does not operate a CDC consumer, MinIO CDC
writer, Silver pipeline, or production Kafka cluster.

## Configure

Copy `.env.example` to ignored `.env`, replace local placeholder passwords, and keep the connector
role distinct from `POSTGRES_USER`. Required groups are:

```text
KAFKA_CLUSTER_ID, KAFKA_BOOTSTRAP_SERVERS, KAFKA_EXTERNAL_PORT
KAFKA_CONNECT_PORT, KAFKA_CONNECT_URL, KAFKA_TOPIC_PREFIX
KAFKA_DEFAULT_PARTITIONS, KAFKA_RETENTION_MS
DEBEZIUM_CONNECTOR_NAME, DEBEZIUM_SLOT_NAME, DEBEZIUM_PUBLICATION_NAME
DEBEZIUM_DATABASE_HOST, DEBEZIUM_DATABASE_PORT, DEBEZIUM_DATABASE_NAME
DEBEZIUM_DATABASE_USER, DEBEZIUM_DATABASE_PASSWORD
DEBEZIUM_HEARTBEAT_INTERVAL_MS, DEBEZIUM_SNAPSHOT_MODE
CDC_HTTP_TIMEOUT_SECONDS, CDC_HTTP_MAX_ATTEMPTS
```

Do not commit `.env`, put credentials in CLI arguments, or paste a connector config/database URL into
logs. Host Kafka and Connect ports bind only to loopback.

## First start and initial snapshot

For snapshot evidence, populate PostgreSQL before first connector registration:

```bash
make postgres-up
make generate-data GENERATOR_ARGS="--once --seed 20260722 --customers 50 --merchants 15 --transactions 250"
make cdc-up
```

`cdc-up` waits for PostgreSQL/Kafka/Connect health, builds the pinned one-shot bootstrap image,
reconciles the non-superuser CDC role/publication, validates connector config, and creates or updates
the connector. A successful replay reports `role_created=false`, `publication_created=false`, and
connector action `unchanged`.

## Health and status

```bash
docker compose --env-file .env ps postgres kafka kafka-connect connector-init
make cdc-status
docker compose --env-file .env exec kafka \
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list
```

Ready state requires PostgreSQL, Kafka, and Kafka Connect `healthy`, `connector-init` exit code zero,
and both connector and task state `RUNNING`. Expected business topics are exactly the six
`fintech.cdc.payments.<table>` topics documented in the CDC architecture. Heartbeat and Connect
internal topics are infrastructure topics, not business CDC tables. `fintech.cdc.transaction` is
Debezium transaction-order metadata and likewise is not a seventh captured table.

## Connector lifecycle

```bash
make cdc-register                  # validate + create/update/unchanged
make cdc-status                    # connector/task status without config or traces
make cdc-restart                   # restart connector/tasks and wait for RUNNING
make cdc-delete CONFIRM=1          # guarded development deletion; slot retained
```

`cdc-delete` is destructive to connector execution but intentionally leaves the PostgreSQL slot and
Kafka records. Re-register to resume. Do not manually drop an active slot. Full Kafka/slot offset
reset is deliberately not automated because deleting only one side can create data loss, a duplicate
snapshot, or an unusable retained slot.

## Inspect topics safely

```bash
make cdc-inspect CDC_TABLE=payment_transactions
python scripts/cdc/inspect_topic.py --table refunds --max-messages 20 --timeout-ms 10000
```

Inspection starts from the beginning, is bounded, and creates no durable consumer group. It prints
only primary key, operation, source table/snapshot/LSN/timestamps/transaction ID, and Kafka
partition/offset. Use Kafka's console consumer directly only in a controlled local test; raw values
can contain names, emails, financial references, and complete rows.

## Verify source changes

Generate a new unique seed after the connector is running, or use the opt-in integration suite:

```bash
make generate-data GENERATOR_ARGS="--once --seed 20260723 --customers 10 --merchants 5 --transactions 30"
make cdc-inspect CDC_TABLE=customers
make cdc-inspect CDC_TABLE=payment_transactions
RUN_CDC_INTEGRATION=1 pytest -m cdc_integration
```

The suite verifies snapshot `r`, insert `c`, update `u`, allowed delete `d` plus tombstone,
transaction-event/refund inserts, exact Decimal encoding, UTC timestamp schema, LSN/transaction
metadata, idempotent bootstrap, and restart continuity.

## Stop, restart, and replay

```bash
make cdc-down
make cdc-up
```

`cdc-down` removes only connector-init/Kafka Connect/Kafka containers. It does not stop or remove
PostgreSQL/MinIO and does not delete the Kafka named volume. On restart, Connect resumes from its
Kafka offset topic and the retained PostgreSQL slot. Diagnostic inspection re-reads historical topic
records but does not change connector offsets.

## Troubleshooting

- **Kafka unhealthy:** inspect `make kafka-logs`; verify cluster ID, listeners, local port, and named
  volume permissions.
- **Connect unhealthy:** inspect `make connect-logs`; verify Kafka health and pinned image/plugin.
- **Connector FAILED:** run `make cdc-status`, then inspect Connect logs. Status output omits task
  traces by design; logs must be treated as confidential and must not contain the connector password.
- **Validation says `name` missing:** use the repository registration script; its validate payload
  includes Connect's required internal name exactly once.
- **Publication/permission failure:** rerun `make cdc-up`; confirm the configured CDC user differs
  from `POSTGRES_USER` and the publication includes all six tables.
- **No snapshot `r`:** `initial` runs only when no prior connector offset exists. Populate the source
  before first registration; ordinary restart must not repeat the snapshot.
- **WAL growth:** restore Connect promptly and inspect `pg_replication_slots`. Do not drop a slot
  until impact and replay position are understood.
- **No new event after restart:** confirm connector/task `RUNNING`, slot active, publication table
  membership, and that the source transaction committed.

## Known limitations / out of scope

One broker, one worker/task, replication factor one, plaintext local transport, no SASL/ACL/TLS,
Schema Registry, DLQ, capacity/SLO evidence, HA, backups, or automated WAL/lag alerting. Kafka CDC
consumer, MinIO publication, Airflow, Spark/Flink, dbt, Snowflake, dashboards, reconciliation, and
observability are out of scope for Phase 4.
