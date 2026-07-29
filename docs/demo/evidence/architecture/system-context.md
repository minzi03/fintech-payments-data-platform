# System-context evidence

| Field         | Value                                                |
| ------------- | ---------------------------------------------------- |
| Provenance    | `generated-from-source`                              |
| Source commit | `11ba97f539f9f3ed7e16c2e772a9988afce7e9e5`           |
| Sanitization  | `reviewed-safe`                                      |
| Runtime proof | No; architecture derived from tracked implementation |

```mermaid
flowchart TB
    subgraph DataPlane["Data plane"]
        PG["Payments PostgreSQL"] --> WAL["WAL / pgoutput"]
        WAL --> DBZ["Debezium"]
        DBZ --> KAFKA["Kafka topics"]
        KAFKA --> CDC["Manual CDC consumer"]
        CSV["Settlement CSV"] --> VALIDATE["Contract validation"]
        CDC --> BRONZE["Immutable Bronze"]
        VALIDATE --> BRONZE
        VALIDATE --> QUARANTINE["Private quarantine"]
        BRONZE --> SILVER["PyArrow Silver + quality"]
    end

    subgraph Control["Orchestration/control"]
        AIRFLOW["Airflow"] --> VALIDATE
        AIRFLOW --> CDC
        AIRFLOW --> SILVER
        AIRFLOW --> CONTROLDB["PostgreSQL control state"]
        MANIFESTS["Component SQLite manifests"]
    end

    subgraph Portal["Portal security/runtime"]
        BROWSER["Browser"] --> WEB["Next.js Web"]
        WEB --> API["FastAPI API"]
        API --> OIDC["Keycloak / OIDC"]
        API --> PORTALDB["Portal PostgreSQL"]
        API --> REDIS["Redis abuse state"]
        WORKER["Audit worker"] --> PORTALDB
    end
```

There is deliberately no Portal-to-data-plane edge. The Portal does not operate Kafka, MinIO,
Airflow, or Silver.
