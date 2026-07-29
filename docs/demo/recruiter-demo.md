# Five-minute recruiter and hiring-manager demo

## Objective

Communicate the business problem, engineering depth, and truthful maturity without requiring live
infrastructure.

## Required services

None. Use repository-contained evidence. If the prepared environment is already healthy, one Portal
or Airflow screen may supplement the package but is never required.

## Flow

| Time        | Show                              | Say                                                                  |
| ----------- | --------------------------------- | -------------------------------------------------------------------- |
| 00:00–00:45 | README business problem           | Payments need reliable CDC and partner settlement evidence           |
| 00:45–01:30 | System-context asset              | Two data paths converge at immutable Bronze and typed Silver         |
| 01:30–02:30 | CDC and batch summaries           | Deterministic identities, quarantine, upload-before-commit           |
| 02:30–03:15 | Airflow and state ownership       | Scheduling/control state does not replace component authority        |
| 03:15–04:10 | Portal boundary                   | Separate OIDC/session/abuse/audit runtime; no data-plane adapters    |
| 04:10–04:40 | Security and validation summaries | Bounded scanning and destructive-test isolation                      |
| 04:40–05:00 | Limitations                       | Local/reference; warehouse, HA/DR, production authorization deferred |

## Prepared state and live actions

Prepared state is the tracked evidence package at source commit
`11ba97f539f9f3ed7e16c2e772a9988afce7e9e5`. There are no live write actions.

## Expected output

The reviewer should understand:

- the source-to-Silver boundary;
- why failure/replay semantics are scoped rather than global;
- why the Portal is separate;
- what is implemented versus deferred.

## Failure fallback

If screen sharing, Docker, or network access fails, continue from
[fallback-demo.md](fallback-demo.md). All required assets are text/JSON.

## Cleanup

None. This route performs no runtime mutation.

## Allowed claims

- production-oriented local/reference implementation;
- near-real-time local CDC without a production SLO;
- effectively-once behavior at named immutable publication/delivery boundaries;
- first-party Portal runtime/container hardening.

## Prohibited claims

- production-grade or production-ready;
- globally exactly-once;
- highly available or DR-ready;
- warehouse/dbt/dashboard implemented;
- Portal data-platform control plane;
- vulnerability-free.
