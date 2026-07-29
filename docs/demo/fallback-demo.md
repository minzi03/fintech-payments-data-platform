# Offline architecture and evidence walkthrough

## Guarantee

This path works from a checkout without Docker, network, PostgreSQL, Kafka, MinIO, Airflow,
Keycloak, Redis, or Portal runtime.

## Route

1. Open the [root README](../../README.md) for the business problem and capability matrix.
2. Open the [system-context asset](evidence/architecture/system-context.md).
3. Show [batch contract evidence](evidence/data-platform/batch-contract-summary.json).
4. Show [CDC publication evidence](evidence/data-platform/cdc-publication-boundary.json).
5. Show [Silver/quality evidence](evidence/data-platform/silver-quality-summary.json).
6. Show [Airflow DAG evidence](evidence/data-platform/airflow-dag-summary.json).
7. Explain the [Portal boundary](evidence/portal/runtime-boundary.md).
8. Show the [dead-letter health capture](evidence/portal/dead-letter-health.json).
9. Show [security](evidence/security/security-summary.json) and
   [validation safety](evidence/validation/validation-safety.json).
10. Close with the [claims script](claims-script.md) and known limitations.

## Evidence interpretation

| Provenance                           | Interpretation                                            |
| ------------------------------------ | --------------------------------------------------------- |
| `live-captured`                      | Read-only output captured from the baseline runtime       |
| `generated-from-source`              | Deterministic summary of tracked implementation/contracts |
| `derived-from-recorded-verification` | Sanitized summary of committed checkpoint evidence        |
| `illustrative`                       | Synthetic example for explanation, never runtime proof    |

The evidence manifest provides SHA-256 identity, source commit/tag, sanitization status, expected
result, reproduction command, and limitations for each asset.

## Failure map

| Live failure                     | Offline asset                         | What to say                                          |
| -------------------------------- | ------------------------------------- | ---------------------------------------------------- |
| Docker/network unavailable       | System-context asset                  | Architecture can be reviewed without runtime         |
| Kafka unavailable or CDC delayed | CDC boundary asset                    | Show implemented protocol, not a fresh-event claim   |
| MinIO unavailable                | CDC/Silver assets                     | Show object/lineage contracts, not live object state |
| Silver not ready                 | Silver summary                        | Prepared semantics, not live completion              |
| Airflow unavailable              | DAG summary                           | Static DAG inventory; no live run claim              |
| Keycloak/Portal unavailable      | Portal boundary                       | Explain trust/authority without login                |
| Worker health unavailable        | Dead-letter capture                   | State capture time and do not claim current liveness |
| Scanner output too large         | Security summary                      | Show bounded policy summary, never raw findings      |
| Screen sharing fails             | Open files locally or send repository | Package remains asynchronous-reviewable              |

## Cleanup and mutation

This route has no live actions, cleanup, or persistent impact. It does not alter Portal state or the
intentional dead letter.

## Limitations

- Runtime captures are point-in-time evidence bound to the source checkpoint.
- Generated/source summaries demonstrate implementation structure, not current service health.
- Illustrative prepared data is not proof that a live command ran.
- No production deployment, HA/DR, warehouse analytics, or production authorization is implied.
