# PR-PORTAL-002 Authorization Matrix

- Status: **ACCEPTED — FROZEN**
- Revision: 1
- Policy model: deny by default; only `ALLOW` grants access

This normalized matrix is the source for generated parameterized policy tests. It avoids a
meaningless full Cartesian table while preserving every decision dimension.

## 1. Context gates applied before role grants

| Gate            | Condition                                                     | Result                             |
| --------------- | ------------------------------------------------------------- | ---------------------------------- |
| Authentication  | No current ACTIVE session                                     | DENY / `AUTHENTICATION_REQUIRED`   |
| Session         | Idle/absolute expired, revoked, invalid, wrong security epoch | DENY / corresponding session code  |
| Tenant          | Missing, unknown, or resource tenant mismatch                 | DENY or masked 404                 |
| Environment     | Missing for scoped action                                     | DENY / `ENVIRONMENT_REQUIRED`      |
| Entitlement     | Environment not in current verified entitlement set           | DENY / `ENVIRONMENT_ACCESS_DENIED` |
| Assurance       | Production without AAL2                                       | DENY / `STEP_UP_REQUIRED`          |
| Policy          | Unknown action/resource or revision unavailable               | INDETERMINATE / fail closed        |
| Capability      | Unknown, disabled, planned, stale, or incompatible            | DENY according to capability code  |
| Capability mode | Mutation against READ_ONLY or DEGRADED                        | DENY                               |
| Resource        | Classification/purpose/ownership obligation unsatisfied       | DENY                               |

These gates cannot be overridden by a role.

## 2. Anonymous principal

| Action                              | Decision | Requirements                                         | Audit                                  |
| ----------------------------------- | -------- | ---------------------------------------------------- | -------------------------------------- |
| `portal.authenticate`               | ALLOW    | Valid login intent, same origin, configured provider | Login-start event                      |
| Health liveness/readiness aggregate | ALLOW    | Safe operational contract only                       | Telemetry                              |
| Safe build information              | ALLOW    | No dependency or identity details                    | Telemetry                              |
| Every other action                  | DENY     | None                                                 | Denial when protected API is attempted |

## 3. Role grants

`Y` means the role may proceed to contextual gates. It is not unconditional authorization.

| Action                            | portal_viewer | data_engineer_viewer | platform_operator_viewer | security_auditor_viewer | portal_admin_viewer |
| --------------------------------- | :-----------: | :------------------: | :----------------------: | :---------------------: | :-----------------: |
| `portal.home.read`                |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.session.read`             |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.logout`                   |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.environment.list`         |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.environment.select`       |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.capability.read`          |       Y       |          Y           |            Y             |            Y            |          Y          |
| `portal.system_status.read`       |       Y       |          Y           |            Y             |            Y            |          Y          |
| `source.list` / `source.read`     |               |          Y           |            Y             |                         |          Y          |
| `cdc.connector.list`              |               |          Y           |            Y             |                         |          Y          |
| `dataset.list` / `dataset.read`   |       Y       |          Y           |            Y             |            Y            |          Y          |
| `pipeline.list` / `pipeline.read` |               |          Y           |            Y             |                         |          Y          |
| `audit.read`                      |               |                      |                          |            Y            |          Y          |
| `admin.read`                      |               |                      |                          |                         |          Y          |

Future source, CDC, dataset, pipeline, audit, and admin actions still deny while their capability
is `PLANNED`, regardless of role.

## 4. Environment rules

| Environment      | Entitlement                                           | Assurance                  | Initial PR-002 restrictions                    |
| ---------------- | ----------------------------------------------------- | -------------------------- | ---------------------------------------------- |
| `local`          | Explicit mapped entitlement or local test principal   | AAL1                       | Read-only Portal capabilities                  |
| `development`    | Explicit mapped entitlement                           | AAL1                       | Read-only Portal capabilities                  |
| `staging`        | Explicit mapped entitlement                           | AAL1; AAL2 for audit/admin | Read-only; audit/admin only for matching roles |
| `production`     | Explicit production entitlement independent from role | AAL2                       | Read-only pilot; no production mutation        |
| Unknown/disabled | Never valid                                           | N/A                        | DENY                                           |

Environment selection never creates an entitlement or changes a role.

## 5. Tenant rules

| Tenant context                                                                      | Decision                                                           |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Session, environment, capability, and resource all equal `fintech-platform-primary` | Continue evaluation                                                |
| Missing or unknown session tenant                                                   | DENY                                                               |
| Resource tenant differs                                                             | Masked 404 for protected resource; 403 for explicit administration |
| Browser attempts to submit tenant                                                   | Ignore and resolve server-side; mismatch attempt is audited        |

## 6. Capability-state rules

| State                   | Read                                             | Mutation                                      | Visibility                                          |
| ----------------------- | ------------------------------------------------ | --------------------------------------------- | --------------------------------------------------- |
| AVAILABLE               | Subject to policy                                | Unsupported in PR-002; future policy required | Authorized projection visible                       |
| READ_ONLY               | Subject to policy                                | DENY                                          | Visible with read-only mode                         |
| DEGRADED                | Last verified safe read only when policy permits | DENY                                          | Visible with timestamp/banner                       |
| PLANNED                 | DENY operational API                             | DENY                                          | Hidden; optional roadmap hint to authorized preview |
| DISABLED                | DENY                                             | DENY                                          | Hidden; administrators may receive safe reason      |
| Unknown/stale authority | DENY or safe degraded read only                  | DENY                                          | Fail closed                                         |

## 7. Decision/audit rules

| Decision class               | Audit behavior                                                |
| ---------------------------- | ------------------------------------------------------------- |
| Login/session transition     | Full append-only event                                        |
| DENY or INDETERMINATE        | Full append-only event                                        |
| Production access decision   | Full append-only event                                        |
| `audit.read` or `admin.read` | Full append-only event                                        |
| Non-production R0 ALLOW      | Aggregated telemetry unless resource policy requires evidence |

## 8. Test generation

Parameterized tests must combine:

- every role row, including no role and unknown role;
- four environments plus missing/unknown/disabled;
- valid, invalid, and missing tenant;
- all five capability states plus unknown/stale;
- AAL1/AAL2 and expired identity verification;
- known/unknown action and resource type;
- policy/capability revision match and mismatch.

Every combination asserts decision, reason code, HTTP mapping, audit requirement, and absence of
an ALLOW result for unknown or indeterminate state.
