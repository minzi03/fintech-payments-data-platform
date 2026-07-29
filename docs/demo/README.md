# Canonical demo and evidence package

## Purpose

This directory is the canonical interview and asynchronous-review package for the repository.
Choose the shortest route that fits the audience:

| Audience                    |     Duration | Entry point                          |
| --------------------------- | -----------: | ------------------------------------ |
| Recruiter or hiring manager |    5 minutes | [Recruiter demo](recruiter-demo.md)  |
| Technical interviewer       |   10 minutes | [Canonical demo](canonical-demo.md)  |
| Deep technical review       |   20 minutes | [Deep-dive demo](deep-dive-demo.md)  |
| No Docker/network/runtime   | 8–12 minutes | [Offline fallback](fallback-demo.md) |

[Technical operator notes](technical-demo.md), [preflight](preflight.md), and the
[claims script](claims-script.md) support the presenter. The machine-readable
[evidence manifest](evidence/manifest.json) binds every evidence asset to the FF-04 source
checkpoint.

## Demo strategy

The preferred sequence is:

```text
prepared data-platform evidence
  + one optional bounded live CDC change
  + a short, separate Portal security walkthrough
```

If any live prerequisite is unavailable or unsafe, switch immediately to:

```text
architecture and evidence-only walkthrough
  using tracked sanitized text/JSON assets
```

Cold full-stack startup, reset, migration downgrade, broad replay, volume deletion, scanner dumps,
and intentional dead-letter repair are not demo steps.

## Boundary model

The package always separates:

1. data plane;
2. Airflow orchestration/control state;
3. Portal security/runtime;
4. cross-cutting security and evidence.

The Portal has no implemented operational adapter for Kafka, MinIO, Airflow, or Silver. Warehouse,
dbt, Gold, dashboards, production HA/DR, and production authorization are deferred.

## Evidence inventory

The package uses twelve high-value text/JSON assets:

- system-context architecture;
- synthetic prepared-demo namespace;
- settlement contract summary;
- CDC publication boundary;
- Silver/quality semantics;
- Airflow DAG inventory;
- Portal trust/authority boundary;
- live-captured dead-letter health;
- security-policy summary;
- validation-safety summary;
- verification checkpoint summary;
- offline rehearsal record.

No screenshots are included. Text and JSON provide the same review value without browser, desktop,
EXIF, credential, profile, or unrelated-window leakage.

Each asset is classified as:

- `live-captured`;
- `generated-from-source`;
- `derived-from-recorded-verification`;
- `illustrative`.

Illustrative data is explicitly synthetic and is never presented as runtime proof.

## Existing detailed guides

The older [phase demo guide](demo-guide.md), [25–28 minute script](demo-script.md), and
[phase checklist](demo-checklist.md) remain useful detailed references. They are superseded as the
canonical reviewer route by this package and must not be mistaken for a requirement to reset or
start the complete stack during an interview.

## Safety invariants

- Never show `.env`, credentials, tokens, cookies, emails, raw provider claims, or audit payloads.
- Never use `docker compose down -v`, migration downgrade, broad reset, or persistent database
  cleanup.
- Use the dedicated synthetic seed/namespace documented in
  [prepared-demo-dataset.json](evidence/data-platform/prepared-demo-dataset.json).
- Treat the live seed as one-use per persistent database. If it already exists, use prepared
  evidence instead of deleting rows.
- Preserve the intentional audit dead letter.
- Do not claim global exactly-once, warehouse analytics, production scale, HA/DR, or production
  authorization.

## Canonical sources

- [Root reviewer entrypoint](../../README.md)
- [Current architecture](../architecture/current-state.md)
- [Approved claims](../architecture/claims.md)
- [Portal troubleshooting](../portal/troubleshooting.md)
