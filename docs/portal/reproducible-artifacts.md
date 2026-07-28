# Reproducible Portal artifacts

## Scope

S06-01 establishes immutable build inputs and measures the output identity of the Portal API
and Portal Web images. It does not publish, sign, attest, scan, deploy, or generate an SBOM.
The API image is also the image used by the audit worker and migration job.

The repository has two Portal build contexts, both rooted at the repository:

| Image | Dockerfile | Runtime |
| --- | --- | --- |
| `fintech-portal-api` | `apps/portal-api/Dockerfile` | Python 3.11 API, migration job, audit worker |
| `fintech-payments-data-platform-portal-web` | `apps/portal-web/Dockerfile` | Node.js 22 standalone Next.js server |

`docker-compose.yml` is the local orchestration entry point. `.github/workflows/ci.yml` is the
only tracked CI workflow and contains the quality, image, and runtime-health build path.

## Immutable inputs

### Python

The repository continues to use `pip`; no application package manager was replaced.

- `requirements.lock` contains the 43 fully resolved runtime packages used by Portal API and
  the audit worker.
- `requirements-dev.lock` contains those same versions plus 12 development packages.
- Every package is pinned with `==` and has one or more SHA-256 distribution hashes.
- The runtime Docker build accepts binary distributions only. A missing compatible wheel is
  therefore a hard build failure rather than an unpinned source build.

Both files are compiled for CPython 3.11 on Linux using `pip-tools==7.5.2` inside the pinned
Python build image. Hashes cover published distributions across supported platforms, while
dependency selection and image verification target `linux/amd64`.

Validate the committed lock metadata:

```bash
python scripts/portal/lock_portal_dependencies.py --check
```

Regenerate both locks after an intentional `pyproject.toml` dependency change:

```bash
python scripts/portal/lock_portal_dependencies.py --update
git diff -- apps/portal-api/requirements.lock apps/portal-api/requirements-dev.lock
```

The update command mounts the repository read-only into the pinned Linux/Python 3.11 image.
Review all direct and transitive changes before committing. CI installs the development lock
with `pip --require-hashes`; the runtime image builds a wheelhouse from the runtime lock with
the same enforcement.

### JavaScript

pnpm remains the package manager. The repository pins pnpm 11.9.0, uses the committed
`pnpm-lock.yaml` integrity records, and installs with `pnpm install --frozen-lockfile` in both
CI and the image build.

### Base images and CI actions

Dockerfiles retain readable version tags and append the verified multi-architecture OCI index
digest. The Dockerfile frontend is pinned the same way. This lets Docker select the correct
platform manifest while preventing tag movement.
The Portal jobs in `.github/workflows/ci.yml` pin third-party actions to full 40-character
commit SHAs and retain version comments for maintenance.

When updating a base image or action:

1. Resolve the tag from the official registry or source repository.
2. Verify the multi-architecture index includes the target architecture.
3. Update the readable tag, immutable digest or SHA, and maintenance comment together.
4. Run the lock, unit, image, and two-build checks below.

## Build and manifest workflow

Run:

```bash
python scripts/portal/build_reproducible_artifacts.py \
  --output build/portal-artifacts/manifest.json
```

The command performs two `--pull --no-cache` builds of each Portal image for `linux/amd64`,
disables BuildKit SBOM/provenance output reserved for later Sprint 06 tasks, compares the two
local OCI image configuration/root-filesystem identities, inspects the runtime images, and
writes schema `portal-artifact-build/v1`.

The generated manifest records:

- repository name, branch, and source commit;
- workflow, dependency-lock, pnpm-lock, and Dockerfile SHA-256 identities;
- pinned base-image references and pinned Portal action commits;
- build-tool versions and fixed source-date epoch;
- both clean-build digests and the final local image digest;
- non-root and forbidden-content inspection results.

The wall-clock generation timestamp is manifest metadata only and is never passed to an image
build. The manifest rejects sensitive field names and host-specific filesystem paths. It never
contains secrets, credentials, CI token claims, usernames, or raw environment configuration.
Generated manifests live under the already ignored `build/` directory and become formal
release inputs only in a later signing/provenance task.

## Reproducibility definitions

Repository determinism means dependency resolution, base images, CI actions, Dockerfiles,
source revision, build arguments, and package-manager inputs have immutable identities.

OCI byte determinism is measured separately. S06-01 treats two equal local image IDs as proof
that image configuration and referenced root-filesystem layers are byte-identical. When either
image differs, the command preserves both observed digests, compares bounded application-file
hashes, and records `oci_image_identity: not_verified` plus the blocking paths and reason. Use
`--require-identical` when a downstream gate must reject that state; it must never be reported
as a pass based only on successful builds.

The Portal Web build uses the source commit as the Next.js build ID and passes the commit time
as `SOURCE_DATE_EPOCH`. The Portal API copies application source after installing only
hash-verified dependency wheels, avoiding nondeterministic local application-wheel metadata.

The current measured limitation is security-relevant and intentional: Next.js 16 generates
random preview-mode and server-action cryptographic material during each clean build. That
changes the prerender and server-reference manifests. S06-01 does not replace those values with
predictable source-derived keys or record their contents. A secure, identical multi-replica Web
artifact requires an approved secret-input lifecycle in a later task. The API application and
dependency file contents can remain identical while local Docker-exporter layer metadata still
changes; both states are reported separately.

## Image-content checks

The verifier requires a declared non-root runtime user and rejects application-root copies of:

- Git or GitHub metadata;
- `.env`;
- tests and browser reports;
- pytest, mypy, Ruff, pip, or pnpm caches.

It also rejects sensitive environment-field names in the final image configuration. This is a
bounded application-image check, not the vulnerability, license, SBOM, or secret-scanning
policy planned for later Sprint 06 tasks.

## CI behavior

The Portal quality job installs only hash-locked Python dependencies and validates that:

- every direct runtime/development requirement is present and satisfies `pyproject.toml`;
- every locked package has a SHA-256 hash;
- runtime packages have identical versions in the development lock.

The Portal container job performs the two clean builds, writes the build manifest, reports OCI
identity as verified or blocked with evidence, and then starts Compose with `--no-build` so
health checks exercise the measured images. Input integrity, hash locking, pinned identities,
image inspection, and runtime health are hard gates. OCI identity becomes a hard gate with
`--require-identical` after the remaining cryptographic-input contract is approved.

## Local verification

```bash
python scripts/portal/lock_portal_dependencies.py --check
python -m ruff check apps/portal-api scripts/portal
python -m ruff format --check apps/portal-api scripts/portal
python -m mypy --config-file apps/portal-api/pyproject.toml
python -m pytest -c apps/portal-api/pyproject.toml apps/portal-api/tests
pnpm install --frozen-lockfile
pnpm --filter @fintech/portal-web format:check
pnpm --filter @fintech/portal-web lint
pnpm --filter @fintech/portal-web typecheck
pnpm --filter @fintech/portal-web test
python scripts/portal/build_reproducible_artifacts.py \
  --output build/portal-artifacts/manifest.json
git diff --check
```

Known limitations are explicit: S06-01 validates one target platform (`linux/amd64`), emits a
local non-signed manifest, reports rather than conceals the current OCI nondeterminism, and does
not claim registry digest stability across different builder implementations. Multi-platform
build parity, secure deterministic Next.js cryptographic inputs, vulnerability policy, SBOM,
signing, formal provenance, publication, and deployment promotion remain outside this task.
