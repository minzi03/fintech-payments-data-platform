# Portal secrets provider boundary

S06-03 keeps raw environment-backed values in the dedicated immutable
`EnvironmentSecretInputs` model. API and audit-worker role aggregates contain only the provider ID,
logical reference identities and rotation versions. A non-environment provider fails closed when
conflicting inline environment secret values are present.

## Scope

S06-02 establishes a synchronous, vendor-neutral boundary between typed Portal configuration and
runtime secret resolution. It does not integrate a cloud KMS, secret manager, external agent,
mounted-file provider, HSM, deployment system, or workload identity.

The existing environment variables remain compatible. Pydantic loads their values as
`SecretStr`; the default `EnvironmentSecretProvider` then resolves those redacted settings through
the same provider contract future adapters must implement. Application composition receives a
startup-resolved bundle rather than reading environment-backed values directly.

## Dependency map

| Logical reference | Purpose | Current consumer | Value kind |
| --- | --- | --- | --- |
| `portal/runtime-database-url` | Portal security-state connection | Runtime SQLAlchemy engine | text |
| `portal/audit-worker-database-url` | Least-privilege archive connection | Audit worker engine | text |
| `portal/oidc-client-secret` | Confidential-client authentication | OIDC provider adapter | text |
| `portal/client-address-hmac-key` | Privacy-safe abuse fingerprints | Trusted-address resolver | 32-byte key |
| `portal/redis-url` | Distributed abuse backend connection | Redis abuse adapter | text |
| `portal/security-master-key` | Derived lookup and wrapping authority | Security material and envelope cipher | 32-byte key |

OIDC access/refresh/ID tokens, opaque sessions, CSRF material, and provider-session envelopes are
runtime security state, not configuration secrets. Their existing PostgreSQL authority and
encrypted envelope lifecycle are unchanged.

## Architecture

```text
PortalApiSettings
  |
  | SecretStr values and non-secret version/configuration metadata
  v
SecretProvider
  |-- start()
  |-- status()
  |-- resolve(exact SecretReference)
  `-- close()
          |
          v
ResolvedPortalSecrets
  |-- redacted typed values
  |-- exact provider/version metadata
  |-- bounded rotation plan
  `-- metadata-only resolution evidence
          |
          +--> runtime/audit database engines
          +--> OIDC provider
          +--> abuse resolver and Redis adapter
          `--> security material and ProtectedValueCipher
```

Resolution is synchronous and occurs once during application or worker startup. The provider is
closed after all required values are resolved. Provider calls never occur on authentication or
request hot paths.

## Provider contract

A provider has a bounded identifier and four lifecycle operations:

1. `start` establishes any adapter-local resources.
2. `status` reports availability using non-secret status metadata.
3. `resolve` returns exactly the requested identity, version, purpose, and value kind.
4. `close` releases adapter-local resources after startup resolution.

Failures use deterministic classifications:

- `unavailable`;
- `not-started`;
- `not-found`;
- `version-mismatch`;
- `invalid-value`;
- `provider-mismatch`;
- `production-restricted`;
- `continuity-failure`.

Error messages contain only provider ID, logical reference identity, requested version, and failure
classification. Provider-returned values and lower-level credentials are never rendered.

Future mounted-file, external-agent, workload-identity, or KMS-envelope adapters implement this
contract in application composition. They must return `ResolvedSecret` instances with metadata
matching the exact requested reference. Portal business/authentication code and the envelope
format do not change.

## Secret reference model

`SecretReference` separates:

- `identity`: stable logical name;
- `version`: exact selection, never an implicit latest lookup;
- `purpose`: bounded domain of use;
- `value_kind`: text or bytes;
- `size_bytes`: required size for binary keys.

`ResolvedSecret` holds the value in `SecretStr` or `SecretBytes`, has an explicitly redacted
representation, and exposes only a type-compatible reveal operation. `SecretMetadata` is safe to
record and cannot contain the resolved value.

## Environment provider

`PORTAL_API_SECRET_PROVIDER` defaults to `environment`. Existing environment variable names,
startup validation, base64url encoding, and `SecretStr` behavior are preserved.

The adapter maps environment-backed settings to the logical references above. Binary HMAC/master
keys must decode to exactly 32 bytes. Database URLs, Redis URLs, and OIDC client credentials retain
their existing validation. An invalid, absent, or differently versioned value fails startup.

The environment adapter is a local/development compatibility mechanism, not the permanent
production mechanism. If a production process enables a secret-bearing Portal feature,
environment-provider resolution fails with `production-restricted`.

## Rotation

The startup rotation plan contains:

- one explicitly selected current key;
- at most one previous key and its existing bounded transition window;
- at most one metadata-only staged future version.

Current, previous, and staged versions must be unique. Current and previous key bytes must also
differ. Startup resolves exact current/previous versions atomically before constructing security
components; no version uses an implicit fallback, and staged material is not fetched or activated.
This avoids silent downgrade and split-brain selection.

New envelopes continue to use the current version. Previous envelopes remain readable only inside
the existing transition window. Unknown, expired, missing, or mismatched versions fail closed. The
AES-GCM envelope format, key derivation, algorithms, and current/previous compatibility are
unchanged.

## Startup validation and evidence

Startup verifies:

- configured provider identity matches the injected adapter;
- provider availability;
- every feature-required reference;
- exact reference version and type;
- binary-key size;
- database and Redis URL shape;
- non-empty OIDC credential;
- current/previous continuity;
- staged-version uniqueness;
- production environment-provider restrictions.

Successful resolution emits `secret_provider_resolved` with provider ID, logical reference
metadata, purposes, value kinds, and rotation versions. This metadata-only event and the safe
`SecretResolutionEvidence` object are suitable inputs for a future audit sink. S06-02 does not
change the append-only audit/outbox implementation.

No health endpoint exposes provider values or resolved credentials. No new trace or metric
attribute carries identity values, URLs, key material, or credentials.

## Adding a future provider

1. Implement `SecretProvider` in a composition/infrastructure module.
2. Use a bounded provider ID matching `PORTAL_API_SECRET_PROVIDER`.
3. Resolve only exact identities and versions; reject implicit latest aliases.
4. Return typed `ResolvedSecret` values with matching `SecretMetadata`.
5. Map vendor errors to the deterministic failure classes without embedding upstream responses.
6. Inject the provider into `create_app` or the audit worker `main` composition boundary.
7. Add provider availability, version mismatch, rotation, redaction, and recovery tests.

Provider-specific credentials, workload identities, caching, renewal, and HA behavior are not
defined by S06-02.

## Validation

```bash
python -m ruff check apps/portal-api
python -m ruff format --check apps/portal-api
python -m mypy --config-file apps/portal-api/pyproject.toml
python -m pytest -c apps/portal-api/pyproject.toml apps/portal-api/tests
docker compose --env-file .env.example config --quiet
git diff --check
```

S06-02 introduces no schema, PostgreSQL role, Redis behavior, OIDC protocol, envelope format,
runtime security policy, container-hardening, deployment, signing, SBOM, or publication changes.
