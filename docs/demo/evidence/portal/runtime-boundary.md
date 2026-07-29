# Portal runtime-boundary evidence

| Field         | Value                                           |
| ------------- | ----------------------------------------------- |
| Provenance    | `generated-from-source`                         |
| Source commit | `11ba97f539f9f3ed7e16c2e772a9988afce7e9e5`      |
| Sanitization  | `reviewed-safe`                                 |
| Runtime proof | No; summary derived from tracked implementation |

```text
Browser
  → Next.js Portal Web
  → FastAPI Portal API
  → Keycloak / OIDC
  → Portal PostgreSQL authority
  → Redis abuse-enforcement state
  → audit delivery worker
```

Authority boundaries:

- OIDC provider owns external identity/provider state.
- Portal PostgreSQL owns local sessions, encrypted provider-token envelopes, durable replay, audit,
  outbox, and maintenance state.
- Redis owns reconstructible counters/penalties, not session or logout authority.
- The browser receives an opaque session and never receives provider tokens.
- Logout and crypto-erasure do not depend on Redis or provider availability.

The Portal has no operational adapter for Kafka, MinIO, Airflow, or Silver and must not be
presented as the data-platform control plane.
