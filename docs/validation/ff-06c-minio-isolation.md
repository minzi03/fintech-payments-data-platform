# FF-06C disposable MinIO validation isolation

## Checkpoint

FF-06C corrects a validation-infrastructure defect. It does not change MinIO client behavior,
Silver processing, production Compose storage, application contracts, or frozen release
artifacts.

```text
Source commit before FF-06C:
35f4996edc29268dc4b0cdedd5f193534adc0456

FF-06C commit:
SELF — resolve phase-portal-finalization-minio-isolation^{commit}

Commit message:
test: isolate disposable MinIO validation storage
```

The canonical command is:

```text
make test-minio-disposable
```

It creates all run-specific files in the operating-system temporary directory. No credentials,
rendered configuration, test logs, or resource identifiers are written into the repository.

## Isolation contract

```text
Unique FF-06C run identity
  → unique Compose project
  → external temporary Compose override
  → unique run-owned network
  → unique run-owned MinIO volume
  → canonical buckets
  → real MinIO/Silver integration tests
  → ownership-checked teardown
```

The override replaces the MinIO service volume list rather than appending a second mount. The
effective `/data` mount must be:

```text
<run-owned-volume>:/data
```

The following volume is forbidden in the effective validation configuration and must never be
mounted by an FF-06C container:

```text
fintech-payments-minio-data
```

### Canonical bucket contract

The disposable instance provides isolation. Bucket names therefore remain identical to the
application contract:

| Purpose    | Environment name          | Canonical value      |
| ---------- | ------------------------- | -------------------- |
| Bronze     | `MINIO_BRONZE_BUCKET`     | `fintech-bronze`     |
| Quarantine | `MINIO_QUARANTINE_BUCKET` | `fintech-quarantine` |
| Silver     | `MINIO_SILVER_BUCKET`     | `fintech-silver`     |

Run-specific bucket renaming is prohibited.

### Ownership labels

Every FF-06C container, volume, and network must carry:

```text
com.fintech.validation=true
com.fintech.validation.phase=ff-06c
com.fintech.validation.run-id=<exact-run-id>
com.fintech.validation.repository=fintech-payments-data-platform
```

Compose project/service/volume labels provide an additional ownership boundary. The launcher
refuses pre-existing resources, foreign labels, unexpected services, incorrect mounts, incorrect
networks, endpoint mismatches, and cleanup of resources owned by another run.

## Verified implementation

Implementation identities at this checkpoint:

```text
Launcher SHA-256:
bb9187e6fee67d3ed28fa24614ffa248731365eab8780bad8c4d25653f709455

Focused tests SHA-256:
bcc96f9ec64bc185835e778074ea3c750d12c2546ba629ee5e30aae42a302a4b
```

Recorded isolated validation:

```text
Run ID:
ff06c-minio-20260729t102827z-e084d402

MinIO/Silver:
9 passed

Previously failing test:
test_real_minio_cdc_entities_decimal_timestamp_and_append_only — PASSED

Canonical buckets:
PRESERVED

Persistent global MinIO volume in effective config:
ABSENT

Persistent global MinIO volume mounted:
NO

Persistent volume Docker metadata and attachment set:
UNCHANGED

Disposable containers after teardown:
0

Disposable volumes after teardown:
0

Disposable networks after teardown:
0

Temporary files:
REMOVED
```

## Release-verification invariants

```text
OpenAPI SHA-256:
9fab4ac5b9e41dab87f648d2e797a99caefe51fefacacf4407df97f694471fb2

Alembic head:
008_audit_outbox_and_maintenance

Frozen Portal API:
sha256:619d53a92abf74a6af53756dd412d1ece9cf23bc59b12a54fc691aa65630e8a3

Frozen Portal Web:
sha256:1553e6a46cdffe8bb029ec4265e5a19fcf68fcc8e099051d66f16e42a83562f7

Artifact manifest:
sha256:27bea0e7d7e6df6f3167c441f6a0bf39fbeaae5d4ec40bea05c336afb8eabcdb

Pinned Trivy DB:
sha256:f6e81714e94ef9becd5e44f25d7b9888e5b7c257303888229d9c4709b30b1a3e

Intentional dead letter:
DEGRADED; dead_lettered: 1; unchanged

Protected untracked allowlist:
9 paths; unchanged and unopened

PR #11:
OPEN; unchanged

Remote before FF-06C commit:
ahead 20; behind 0

Expected remote after FF-06C commit:
ahead 21; behind 0
```

## Startup and teardown gates

Before startup, the launcher:

1. proves the project, volume, and network identity do not already exist;
2. renders the merged Compose model in memory;
3. rejects the persistent MinIO volume anywhere in that effective model;
4. requires exactly one `/data` mount;
5. validates canonical bucket configuration and ownership labels.

After startup, it verifies the actual container mounts, networks, published endpoint, volume
labels, service labels, and persistent-volume attachment set.

Before teardown, every resource is revalidated against the exact run identity. Compose removes
only the validated project containers and network; the launcher separately validates and removes
the exact run-owned volume. Broad Docker cleanup and volume pruning are never used.

## Next FF-06 requirement

The next FF-06 must begin again at `FF-06.1` using this checkpoint as its locked baseline. It must
run `make test-minio-disposable` for the MinIO/Silver regression and must not reuse any result from
the previously blocked FF-06 cycle.
