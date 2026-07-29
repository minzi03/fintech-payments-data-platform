# Canonical 10-minute technical demo

## Objective

Demonstrate a truthful source-to-Silver payments data path, bounded reliability semantics, Airflow
control evidence, and a separate Portal security runtime without resetting persistent state.

## Preconditions

- Run the [fail-closed preflight](preflight.md).
- Prefer already prepared PostgreSQL/Kafka/MinIO/Airflow data.
- Use live CDC only when the dedicated seed is absent and every dependency is healthy.
- Keep [offline fallback](fallback-demo.md) open before screen sharing.

## Timeline

| Time        | Stage            | Action                                                                 | Evidence                     | Fallback                   |
| ----------- | ---------------- | ---------------------------------------------------------------------- | ---------------------------- | -------------------------- |
| 00:00–00:45 | Business problem | Explain payment changes and partner settlement evidence                | README business problem      | System-context asset       |
| 00:45–01:30 | Architecture     | Separate data plane, control state, Portal, and cross-cutting controls | Current architecture diagram | Architecture asset         |
| 01:30–02:15 | Batch            | Show contract, checksum identity, Bronze/quarantine, quality reasons   | Batch summary                | Same asset                 |
| 02:15–03:15 | CDC              | Show prepared source row; optionally create one synthetic transaction  | Generator summary            | Prepared dataset asset     |
| 03:15–04:00 | Kafka            | Show topic, partition, offset, operation, timestamp, sanitized key     | Bounded inspector            | CDC boundary asset         |
| 04:00–04:45 | Bronze           | Show object key pattern, checksum, coordinates, schema/row count       | Bounded inspector            | CDC boundary asset         |
| 04:45–05:30 | Silver/quality   | Explain history/latest/current and reason-code evidence                | Bounded inspector            | Silver summary             |
| 05:30–06:15 | Airflow/control  | Show four DAG names, dependencies, latest prepared status              | UI/list only if ready        | DAG summary                |
| 06:15–07:00 | Reliability      | Explain upload-before-commit, identities, replay, quarantine           | Architecture/recovery docs   | CDC boundary asset         |
| 07:00–08:15 | Portal           | Separate walkthrough of OIDC, opaque sessions, authorities, audit      | Portal UI/status if ready    | Portal boundary asset      |
| 08:15–08:50 | Dead letter      | Explain `DEGRADED` with dependencies `UP` and one poison event         | Read-only health             | Dead-letter asset          |
| 08:50–09:25 | Security/safety  | Show policy summary and disposable migration safeguards                | Safe summaries               | Security/validation assets |
| 09:25–10:00 | Limits/takeaway  | State source-to-Silver boundary and deferred production work           | README limitations           | Claims script              |

## Prepared dataset

The dedicated demo namespace is:

```text
generator seed: 50501
settlement seed: 50501
settlement partner: DEMO1
settlement date: 2026-07-29
```

It is synthetic, deterministic, bounded, and contains only `example.test`-style generator data.
The live generator seed is one-use per persistent database. Do not delete existing rows to reuse it.

## Optional bounded live CDC action

Only after preflight confirms the seed is absent:

```bash
python -m generators.cli --once --seed 50501 --customers 1 --merchants 1 \
  --transactions 1 --invalid-rate 0 --duplicate-rate 0
```

This commits one synthetic payment transaction plus the minimum related synthetic source rows in
one PostgreSQL transaction. Expected exit code is `0`. Expected summary names one transaction and
bounded related entities/events. Persistent impact is the dedicated synthetic source namespace and
its subsequent immutable CDC evidence.

If the seed already exists, the command fails or collides by design. Do not clean the database.
Show the prepared dataset and CDC evidence assets.

The downstream path may be demonstrated live only when the bounded consumer and Silver commands
complete in the available window. Otherwise say explicitly:

> The source mutation is live; downstream Bronze/Silver shown here is prepared evidence from the
> same implemented contracts, not a claim that this interview run completed end to end.

## Safe evidence commands

| Command/action                                                                      | Expected exit | Expected summary                              | Persistent impact                          | Duration                             | Failure interpretation                  | Fallback          |
| ----------------------------------------------------------------------------------- | ------------: | --------------------------------------------- | ------------------------------------------ | ------------------------------------ | --------------------------------------- | ----------------- |
| `python scripts/cdc/connector_status.py`                                            |           `0` | Connector/task safe state                     | None                                       | Seconds                              | CDC unavailable or recovering           | CDC asset         |
| `python scripts/cdc/inspect_topic.py --table payment_transactions --max-messages 5` |           `0` | Bounded sanitized coordinates/operation       | Consumer read only                         | Seconds                              | No new event or Kafka unavailable       | CDC asset         |
| `python -m ingestion.cdc_consumer.cli run --once --max-messages 5`                  |           `0` | Bounded publication summary                   | Bronze object, manifest, committed offsets | Under a minute/environment-dependent | Stop live path; do not reset            | CDC asset         |
| `python -m ingestion.cdc_consumer.cli inspect --storage-backend minio`              |           `0` | Object metadata, checksum, row/schema summary | None                                       | Seconds                              | MinIO/evidence unavailable              | CDC asset         |
| `python -m processing.silver.cli process-cdc --max-objects 1`                       |           `0` | Bounded Silver processing result              | Immutable Silver output/lineage            | Under a minute/environment-dependent | Use prepared Silver evidence            | Silver asset      |
| `python -m processing.silver.cli inspect --storage-backend minio`                   |           `0` | Sanitized output/quality summary              | None                                       | Seconds                              | Silver not prepared                     | Silver asset      |
| `docker compose --env-file .env.example exec airflow-scheduler airflow dags list`   |           `0` | Four expected DAG IDs                         | None                                       | Seconds                              | Airflow unavailable                     | DAG asset         |
| Audit worker healthcheck from troubleshooting                                       |           `0` | Expected `DEGRADED`, one dead letter          | None                                       | Seconds                              | Unexpected state; stop demo mutation    | Dead-letter asset |
| `python scripts/security/scan.py policy`                                            |           `0` | Policy/toolchain valid                        | None                                       | Seconds                              | Security gate cannot be claimed current | Security asset    |

Acceptable variation includes offsets, timestamps, object keys, and run IDs. Never promise a fixed
latency.

## Batch evidence

Show the versioned contract and the prepared summary rather than regenerating files live. Explain:

- checksum/file identity;
- accepted Bronze versus private quarantine;
- row-level reason codes and partial acceptance;
- no reconciliation/dashboard output.

## Portal walkthrough

Keep this separate from the data-plane flow:

1. OIDC Authorization Code with PKCE and server-side token handling.
2. Opaque browser session; PostgreSQL owns lifecycle and replay state.
3. Redis owns reconstructible abuse counters, not sessions.
4. Transactional audit/outbox and worker delivery.
5. `DEGRADED` worker status with one intentional dead letter.
6. Current security-policy summary.

Required positioning:

> The Portal is a separate security/runtime engineering surface and does not operate Kafka, MinIO,
> Airflow, or Silver resources.

## Cleanup boundary

Default cleanup is no cleanup:

- retain immutable synthetic evidence when harmless;
- record the seed and identifiers used;
- do not truncate tables, reset offsets/manifests, delete objects, or remove volumes;
- do not clear the intentional dead letter.

If a future separately approved cleanup is necessary, it must target exact synthetic identifiers
and require explicit operator confirmation. This demo contains no cleanup command.

## Claims

Use [claims-script.md](claims-script.md). The concise takeaway is:

> This local/reference project demonstrates deterministic source-to-Silver data engineering,
> bounded replay and publication semantics, Airflow control evidence, and a separate hardened
> Portal security runtime. Production deployment, HA/DR, warehouse analytics, and production
> authorization remain deferred.
