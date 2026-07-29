# Demo claims script

The canonical classification and review triggers are in
[architecture claims](../architecture/claims.md).

| Stage       | Say                                                                                               | Do not say                                                   | Evidence                    | Qualifier                                                |
| ----------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | --------------------------- | -------------------------------------------------------- |
| Overall     | Production-oriented local/reference payments data platform                                        | Production-grade or production-ready enterprise platform     | README/current architecture | Production deployment and authorization deferred         |
| Batch       | Versioned settlement validation preserves accepted and quarantined evidence                       | Reconciliation product or dashboard is implemented           | Contract/batch summary      | Source-to-Silver engineering boundary                    |
| CDC         | Offsets are committed after immutable Bronze publication succeeds                                 | The entire platform is exactly-once                          | CDC boundary                | Named topic/partition/offset and publication boundary    |
| Bronze      | Conditional immutable writes preserve checksum/coordinate identity                                | Every system has one global immutable state                  | CDC boundary                | Object publication boundary only                         |
| Silver      | History/latest/current and quality evidence are typed and replayable                              | Warehouse/dbt/Gold analytics run                             | Silver summary              | Local PyArrow processing                                 |
| Airflow     | Airflow schedules bounded work and owns scheduler metadata                                        | Airflow owns business data or is highly available            | DAG summary/state matrix    | LocalExecutor/reference topology                         |
| Reliability | Named operations use deterministic identities, retries, quarantine, and fencing                   | Perfect deduplication or global fault tolerance              | Current architecture        | Bounded subsystem semantics                              |
| Portal      | Portal demonstrates OIDC, opaque sessions, abuse controls, audit, and telemetry                   | Portal operates Kafka, MinIO, Airflow, or Silver             | Portal boundary             | Separate security/runtime surface                        |
| Dead letter | One intentional poison event demonstrates dead-letter behavior; service is degraded but available | The system is fully healthy, or the record should be deleted | Health capture              | Database/destination/outbox remain `UP`                  |
| Security    | No blocking findings remain under current policy                                                  | Images are vulnerability-free                                | Security summary            | Bounded exceptions and production authorization deferred |
| Validation  | Destructive migration tests require disposable ownership proof and a one-use token                | Tests can safely target any database                         | Validation summary          | Dedicated workflow only                                  |
| Scale       | Repository includes bounded local 10,000-operation harnesses                                      | 10,000 users, operations/second, or production SLO           | Testing docs                | Failure/concurrency evidence, not benchmark              |

## Closing statement

> The repository demonstrates a source-to-Silver data platform with explicit ownership and
> recovery boundaries, plus a separate hardened Portal security runtime. It is intentionally
> honest about deferred warehouse analytics, production deployment, HA/DR, and production security
> authorization.
