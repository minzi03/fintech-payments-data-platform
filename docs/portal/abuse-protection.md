# Distributed abuse protection

The Portal authentication surface uses Redis for atomic, short-lived abuse enforcement while
PostgreSQL remains the authority for sessions, provider lifecycle transitions, durable logout-token
replay receipts, audit records, and crypto-erasure. Redis loss can reduce the strength of distributed
coordination, but cannot make a locally terminated session valid again.

## Request path and trust boundary

```text
socket peer
  -> explicit trusted-proxy resolution
  -> privacy-safe client-prefix HMAC
  -> typed operation policy
  -> one-round-trip atomic Redis evaluation
  -> allow, generic 429, or operation-specific fallback
  -> PostgreSQL session authority / bounded provider operation
```

`X-Forwarded-For` is ignored in the default `direct` mode and whenever the socket peer is not in an
explicit trusted CIDR. In proxy mode the resolver walks the chain from the trusted ingress toward
the client, rejects malformed or excessive chains, normalizes IPv4-mapped IPv6, aggregates IPv4 to
`/24` and IPv6 to `/64`, then HMACs the prefix with domain separation. Raw addresses are never
stored in Redis, logs, metrics, or audit.

## Redis algorithm and key model

The limiter uses a Lua token bucket and Redis server time. One script reads every required bucket,
refills tokens, calculates all decisions, consumes every bucket only when all permit the request,
updates bounded penalty state, and refreshes every TTL. Multi-key operations use the shared
`{portal-abuse}` cluster hash tag. Script SHAs are cached and an atomic `EVAL` fallback handles
`NOSCRIPT`.

Keys contain only a configured namespace, policy revision, bounded enum names, and keyed
fingerprints. They never contain a token, cookie, authorization code, raw address, subject,
session identifier, provider `sid`, or logout `jti`.

Temporary penalties progress through `NORMAL`, `THROTTLED`, `TEMPORARILY_BLOCKED`, and
`EXTENDED_BLOCK`. Levels and block duration are capped, decay after a quiet period, and always
expire. The committed thresholds are conservative development defaults, not production-final
policy.

## Operation policies

| Operation | Dimensions | Redis-loss behavior |
| --- | --- | --- |
| Login initiation | client prefix, global | bounded local fallback |
| OIDC callback | client prefix, configured provider, global | bounded local fallback |
| Invalid auth traffic | client prefix, global | conservative local fallback; deny on exhaustion |
| Foreground refresh | session, client prefix, provider, global | conservative local fallback |
| Background refresh | session, subject, provider issuer/client/sid, global | local quota and provider concurrency; never client IP |
| Logout request | session, client prefix, global | always complete local logout |
| End-session/revocation | session, configured provider | bounded local provider budget |
| Back-channel logout | configured issuer, keyed `jti`, keyed `sid`/subject, global | signature and PostgreSQL replay authority still run |

Every request denied at the HTTP boundary receives generic Problem Details with `429`,
`Retry-After`, `Cache-Control: no-store`, and `Pragma: no-cache`. The body does not reveal which
bucket rejected the request or whether an identity/session/provider state exists.

## Provider and logout invariants

Background refresh applies abuse policy after a valid PostgreSQL claim and before provider I/O.
Throttle releases the durable lease into a deterministic retry state without consuming the provider
failure budget or changing token generation. A leased Redis semaphore bounds provider concurrency
and only its owner can release it.

Logout always follows this order:

```text
revoke local session -> persist terminal state -> crypto-erase envelope
  -> optionally spend provider cleanup budget -> attempt bounded provider cleanup
```

Redis or provider failure cannot roll back terminal local state. A throttled cleanup is recorded and
tokens remain only in memory long enough to make the immediate attempt; erased tokens are never
re-persisted. Back-channel logout always verifies the signature and commits the PostgreSQL durable
`jti` receipt before the Redis replay accelerator is updated.

## Telemetry and audit

Logical metrics are:

- `portal.abuse.requests`
- `portal.abuse.decisions`
- `portal.abuse.backend.duration`
- `portal.abuse.backend.failures`
- `portal.abuse.penalties`
- `portal.abuse.fallback.activations`
- `portal.abuse.provider.concurrency`
- `portal.abuse.provider.throttled`

Attributes are bounded operation, decision, policy, policy revision, dimension type, backend state,
failure class, penalty level, and fallback mode. Spans cover policy evaluation, Redis enforcement,
fallback evaluation, and provider concurrency acquisition.

Only rejection, escalation, provider throttling, and deduplicated backend-outage transitions enter
the append-only audit ledger. Payloads contain bounded classifications, not raw identities.

## Runtime and incident runbook

Local Compose runs ephemeral Redis on `127.0.0.1:56379`. Its readiness adapter is optional: an
outage reports the dependency and overall state as degraded while explicit fallback rules remain
active. Redis is not a liveness prerequisite.

To inspect the runtime:

```powershell
docker compose ps portal-redis portal-api
docker compose exec portal-redis redis-cli INFO server
docker compose exec portal-redis redis-cli --scan --pattern 'portal:abuse:*'
```

To validate failure and recovery:

```powershell
docker compose stop portal-redis
# Confirm /health/ready reports Redis degraded and local logout remains successful.
docker compose start portal-redis
# Confirm Redis becomes UP and distributed enforcement resumes without an API restart.
```

For provider outage, do not increase retries globally. Confirm background claims are bounded,
provider concurrency does not exceed its configured limit, and local logout still terminates and
erases. For proxy changes, update `PORTAL_API_TRUSTED_PROXY_CIDRS` to the exact deployment topology
before enabling `x_forwarded_for`; never use a catch-all CIDR.

Production requires a separate 256-bit HMAC secret, `rediss://`, ACLs, HA/failover, network policy,
capacity testing, and deployment-specific policy tuning. Dynamic policy administration, adaptive
challenge/CAPTCHA, WAF/CDN integration, long-term analytics, and cross-region coordination are
outside this milestone.
