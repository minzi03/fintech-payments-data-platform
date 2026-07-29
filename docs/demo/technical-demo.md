# Ten-minute technical interview operator notes

The canonical technical demo is [canonical-demo.md](canonical-demo.md). This page is a compact
operator card intended to remain visible beside the presentation.

## Required services

| Segment                | Required for live view                               | Evidence-only substitute               |
| ---------------------- | ---------------------------------------------------- | -------------------------------------- |
| Prepared data platform | PostgreSQL, Kafka/Connect, MinIO                     | Data-platform evidence assets          |
| Live CDC               | Healthy source, connector, Kafka, bounded consumer   | Prepared dataset + CDC boundary assets |
| Silver                 | MinIO and bounded Silver processor                   | Silver summary                         |
| Airflow                | Scheduler plus metadata/control dependencies         | DAG summary                            |
| Portal                 | Web/API, PostgreSQL, Keycloak; Redis may be degraded | Portal boundary + dead-letter assets   |

## Operator sequence

1. Confirm [preflight](preflight.md).
2. Open README architecture and limitations.
3. Open evidence files before any live UI.
4. Run at most one source mutation, using seed `50501`, only if authorized by preflight.
5. Use bounded inspectors; never raw consoles or full payload dumps.
6. Switch to evidence immediately on timeout/error.
7. Keep Portal separate from the data-plane narrative.
8. Finish with [claims-script.md](claims-script.md).

## Expected live impact

The optional generator creates one synthetic transaction plus required related synthetic rows.
Bounded consumer/Silver steps may add immutable demo objects and manifests. No Portal state is
mutated by the canonical demo. The audit dead letter remains unchanged.

## Stop conditions

Stop live actions and use fallback if:

- the seed is already present;
- required service health is unknown;
- the expected worker health differs;
- a command requests a credential or destructive confirmation;
- the target namespace cannot be proven;
- an output contains unsafe data;
- the remaining interview time is insufficient.

## Timing

Each live inspection is expected to take seconds or under a minute, but all duration is
environment-dependent. Slow startup is not a reason to improvise a reset.
