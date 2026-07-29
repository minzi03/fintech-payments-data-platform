# Portal security scanning

## Maturity and boundary

Security scanning covers first-party Portal source, immutable Portal dependency locks, tracked
secrets, exact Portal container images, pinned Portal vendor images, Docker configuration, and
GitHub Actions. It is a regression gate and evidence source, not proof that vulnerabilities are
absent.

Security scanning does not mutate dependencies, source, Git history, runtime state, or PostgreSQL
data. FF-02 performs separately reviewed dependency and image remediation before the scanner
evaluates the rebuilt immutable artifacts. Scanning does not authorize production security policy.
SBOM generation, artifact signing, provenance, registry monitoring, and continuous production CVE
monitoring remain separate work.

## Authority map

| Authority                   | Scope                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------------- |
| Semgrep CE                  | High-confidence first-party Python and TypeScript/JavaScript SAST                     |
| OSV-Scanner v2              | Vulnerabilities in the Portal Python runtime/development locks and pnpm lock          |
| Gitleaks                    | Tracked/index content, bounded commit ranges, and explicit full-history scans         |
| Trivy                       | Exact first-party and Portal vendor images; image secrets; supplemental Docker config |
| zizmor                      | GitHub Actions workflow security                                                      |
| Portal repository verifiers | Authoritative Compose, runtime, identity, and container-hardening semantics           |

The scanners are complementary. Bandit does not duplicate Semgrep, pip-audit and `pnpm audit` do
not duplicate OSV, and unrestricted Trivy filesystem scanning does not duplicate immutable lock
scanning. Generic Trivy configuration findings cannot weaken or replace the Portal verifier.

## Immutable tool identity

`security/scanning/toolchain.json` pins each scanner by human-readable version and multi-platform
OCI digest:

| Scanner     | Version | Immutable authority                                           |
| ----------- | ------- | ------------------------------------------------------------- |
| Semgrep CE  | 1.164.0 | OCI digest plus repository-local ruleset hash                 |
| OSV-Scanner | 2.3.8   | OCI digest and the live `osv.dev` API identity                |
| Gitleaks    | 8.30.1  | OCI digest plus repository-local config hash                  |
| Trivy       | 0.70.0  | OCI digest; vulnerability database identity recorded per scan |
| zizmor      | 1.28.0  | OCI digest and built-in ruleset identity                      |

The Trivy identity intentionally avoids the compromised 0.69.4 release line. New GitHub Actions
must use full commit SHAs. Scanner installation is containerized and never changes application
dependency locks.

`scan.py policy` verifies immutable image syntax, the five required identities, ruleset hashes,
policy version, baseline shape, and exception lifecycle before invoking a scanner. A version or
checksum mismatch exits `25`; it is never reported as a clean scan.

## Controlled inputs and privacy

The scanner wrapper creates `build/security/work/tracked` from the Git index. Local untracked files,
editor settings, workspaces, caches, and user-owned prompts are not copied or scanned. A full
Gitleaks history scan reads Git objects explicitly; it does not scan unrestricted working-directory
content.

Scanner-native output is captured in process memory. It is normalized and then discarded. The
portable report contains only rule identity, normalized repository path, immutable asset identity,
package/version metadata, safe SHA-256 fingerprint, policy classification, and safe references.
Secret match text, surrounding context, authorization headers, credential-bearing URLs, and
machine-specific paths are prohibited. Raw secret reports have zero retention.

Scanner subprocesses have a 15-minute hard timeout. Containers run read-only with all capabilities
dropped and `no-new-privileges`; ordinary scans receive 256 MiB of temporary storage. Exact image
scans receive a bounded 4 GiB scratch ceiling because Trivy must materialize its Java advisory
database when inspecting the pinned Keycloak image. A timeout, storage exhaustion, or database
initialization failure is a scanner failure rather than a clean result.

Generated files live only under ignored `build/security/`:

```text
build/security/
├── fast.json
├── full.json
├── history.json
├── images.json
├── cache/
└── work/
```

CI uploads only the named normalized JSON reports, never `cache/`, `work/`, or scanner-native
output.

## Developer commands

The local and CI entrypoints call the same Python policy engine:

```bash
make security-policy
make security-fast
make security-full
make security-images
make security-history
```

`security-images` requires the S06-01 artifact manifest at
`build/portal-artifacts/manifest.json` and exact local images:

```bash
python scripts/portal/reuse_verified_artifacts.py \
  --handoff security/scanning/final-artifacts.json \
  --output build/portal-artifacts/manifest.json \
  --require-clean
python scripts/security/scan.py images \
  --api-image portal-api:ff06a-c9516f9-20260729a \
  --web-image portal-web:ff06a-c9516f9-20260729a \
  --trivy-cache <approved-local-trivy-cache>
```

The reuse command does not build, pull, retag, or replace an image. It validates the run-scoped
tags, Docker image IDs, revision labels, application-content digests, package-inventory digests,
source relationship, and deterministic manifest identity from
`security/scanning/final-artifacts.json`. The scanner then compares that manifest with the same
current Docker image IDs before exporting ignored temporary archives. A descendant chain containing
only explicitly allowlisted artifact-governance files may attest the same parent artifacts; every
other source mismatch fails closed. Image mode requires an approved local Trivy cache whose exact
database identity matches the committed handoff. It disables vulnerability and Java database
updates, removes scanner network access, and rechecks the database identity before and after every
image scan. Audit-worker and migration use the Portal API image result. PostgreSQL, Redis, and
Keycloak use their immutable Compose digests and a separate vendor policy.

On pull requests, CI supplies `PORTAL_SECURITY_GIT_RANGE` as an exact
`<base-commit>..<head-commit>` pair for bounded Gitleaks history scanning. The wrapper accepts only
two full lowercase commit SHAs. Without that CI value, `security-fast` scans the Git-index snapshot,
which gives local staged coverage without reading unrestricted working-tree or untracked content.
Range and complete-history scans use a temporary bare Git clone below `build/security/work/`;
the local worktree and its untracked paths are never mounted into the scanner.

No command performs autofix, dependency update, image retagging, baseline generation, exception
generation, secret revocation, or history rewriting.

## Finding model and policy

The normalized finding schema is `portal-security-finding/v1`. Classification combines:

- severity: critical, high, medium, low, informational;
- exploitability: known exploited, reachable, likely reachable, unknown, build only, development
  only, not present, affected condition absent;
- fix status: fixed available, mitigation available, no fix, disputed, withdrawn, false positive;
- scope: first-party runtime/development, vendor runtime, test fixture, documentation, generated
  artifact, GitHub workflow;
- regression state: new, baseline, advisory new, resurfaced, expired exception, resolved.

A database-only discovery on unchanged source may be `advisory_new`; it is not represented as a code
regression. A baseline records prior observation only. It does not authorize an above-threshold
finding.

The following remain non-suppressible:

- a confirmed real credential;
- a known-exploited reachable first-party vulnerability;
- a critical privilege, Docker socket, privileged-mode, host-namespace, or digest-pin regression;
- an expired exception;
- scanner, database-freshness, input-identity, or redaction failure.

First-party critical findings require resolution or a policy-authorized path where suppression is
permitted. New/resurfaced high reachable findings block. Portal vendor findings are separated from
first-party ownership; a digest change introducing critical/high findings blocks, while unchanged
vendor findings require bounded release review. Development and test findings are advisory unless
they execute on untrusted CI input or reach production output.

## Baseline and exception lifecycle

`security/scanning/baseline.json` records exact normalized fingerprints and first-observed commit.
It has no wildcard capability. A finding over the threshold also needs an active entry in
`security/scanning/exceptions.json`.

Scanning exceptions use `SCN-NNN` identifiers and remain independent of S06-04 `CH-*` container
exceptions. Every exception requires:

- exact scanner, rule/finding, asset, and fingerprint;
- scope, severity, exploitability, and fix status;
- reason and compensating control;
- owner and approval identity;
- creation and expiry dates;
- review trigger, removal criteria, and evidence.

Wildcard, ownerless, approval-less, expiry-less, metadata-mismatched, or over-duration entries are
invalid. Expired exceptions fail the gate. Confirmed credentials cannot be excepted. Default maximum
lifetimes are 7 days for first-party critical, 14 days for first-party high/fix-available, 60 days
for first-party high/no-fix, 60 days for vendor critical/high, 90 days for development/disputed
entries, 180 days for verified false positives or fake fixtures, and 24 hours for an emergency
scanner outage.

False-positive triage must prove the value or code path cannot be accepted by production, bind the
decision to the exact fingerprint, and define a review trigger. Scanner ignore comments must cite an
active SCN entry.

### Initial baseline triage

The S06-05 initial scan records 28 exact OSV/zizmor fingerprints observed at the locked S06-04
baseline. Production dependency graph inspection classifies `postcss` and `sharp` as transitive
Next.js runtime dependencies; `vite`, `js-yaml`, and the affected `brace-expansion` versions are
development/build-only. This classification is encoded as exact package/version overrides and fails
closed for every package not listed.

Seven high workflow findings were remediated by pinning the affected GitHub Actions and CI service
image; they are not excepted. Five existing high runtime dependency findings have `SCN-001` through
`SCN-005`, each expiring on 2026-08-12. These short exceptions exist only because dependency
remediation is outside S06-05. They do not classify the advisories as false positives or prove them
unreachable. Their removal requires dependency updates and the relevant complete verification
suites.

### Initial image triage

The exact S06-04 Portal API and Portal Web image identities contain 60 pre-existing critical/high
findings. Every finding is preserved as an exact baseline fingerprint and has a matching,
metadata-bound `SCN-006` through `SCN-065` exception. Critical exceptions expire on 2026-08-05,
fixed-available high exceptions expire on 2026-08-12, and no-fix high exceptions expire on
2026-08-28. These exceptions authorize only this scanning checkpoint; they do not establish
reachability, accept the vulnerability risk for a release, or defer remediation indefinitely.

The three digest-pinned vendor images contain 997 advisory findings. They remain separately
classified as vendor runtime evidence and report-only at this non-release checkpoint. A release
must perform an explicit vendor-image review and create bounded, exact exceptions for any blocking
finding that remains; S06-05 does not grant that release approval.

Trivy also reports Debian's documented snakeoil fixture at
`/etc/ssl/private/ssl-cert-snakeoil.key` inside the exact PostgreSQL image. `SCN-066` records the
path- and digest-bound false-positive decision through 2027-01-25. Portal TLS, database
authentication, secrets, and deployment configuration do not consume this fixture. Scanner-native
secret material and context are never persisted in reports or logs.

The earliest active image exception expires on 2026-08-05. Dependency and base-image remediation,
fresh image builds, and the complete validation suite are required to remove these exceptions.
The active exceptions mean this repository is not approved or ready for production deployment.

### FF-02 current image disposition

FF-02 rebuilt the exact first-party artifacts from remediation commit
`534f5b70d677333299f0fa12a3011168f332a05c`:

| Artifact                                | Exact image identity                                                      |
| --------------------------------------- | ------------------------------------------------------------------------- |
| Portal API, audit worker, and migration | `sha256:b46b4518c0933c547fd7c09416efc8391bb342b566f65e0d6a1dc69aec2572bd` |
| Portal Web                              | `sha256:663ad676c0e5d632f6a21d399bf73bf7242bea2afdcf75928c34066f0b83791a` |

The scan used Trivy 0.70.0 and vulnerability database identity
`sha256:cee72afaa19faad1252d37bbb98a4f7eb23bc830e7dec8b9f406bc77d7b105e4`.
The immutable build manifest identity is
`sha256:0508cdd8a130bbffceb907b282cf0e686c94f8be179feb1157728346dec1e432`.

The remediation upgraded `cryptography`, `postcss`, `sharp`, and the exact Node base image. It
purged the unused SQLite runtime library from the Portal API image after confirming that the
production application does not use SQLite and the package has no image reverse dependencies. It
did not run a broad operating-system upgrade. The exact first-party Critical/High inventory changed
from 13 Critical and 47 High findings to 10 Critical and 35 High findings. No first-party
Critical/High finding with a scanner-recorded fix remains.

Every remaining first-party Critical/High finding requires an exact rule keyed by image digest,
advisory, binary package, and package version. Missing, duplicate, or unused rules invalidate the
policy. Current disposition is:

| Disposition                           | Count | Evidence boundary                                                                                                                                | Expiry     |
| ------------------------------------- | ----: | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- |
| Affected condition not present        |    35 | MiniZip not built; affected Perl modules/version/architecture absent; or source-package sibling attribution does not contain the affected binary | 2027-01-25 |
| No fix; indirect reachability unknown |    10 | Affected package/code is present, direct Portal entrypoint use was not found, and complete indirect non-reachability was not claimed             | 2026-09-27 |

The 35 `affected_condition_absent` decisions use exact false-positive exceptions and must be
reopened if any image digest, package version, advisory condition, architecture, or scanner
database identity changes.
The 10 unknown-reachability High/no-fix decisions retain bounded risk acceptance. They rely on the
non-root, read-only, capability-free container boundary and require an exact-image rescan when
upstream fixes become available. Neither group is a wildcard, a vulnerability-free claim, or an
authorization for production.

The register now contains 46 active records: the 45 first-party dispositions above plus the
existing exact PostgreSQL snakeoil false positive. Superseded `SCN-001` through `SCN-065` records
were removed; their historical rationale remains in the initial triage sections and Git history.
Vendor findings remain report-only under the unchanged vendor policy.

### Final artifact evidence rebinding

FF-06A selected exactly one final build of each first-party image from source commit
`c9516f9455d90be24dccd62869c2d5afd8b5f802`:

| Artifact                                | Run-scoped local tag                 | Canonical policy identity                                                 |
| --------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------- |
| Portal API, audit worker, and migration | `portal-api:ff06a-c9516f9-20260729a` | `sha256:619d53a92abf74a6af53756dd412d1ece9cf23bc59b12a54fc691aa65630e8a3` |
| Portal Web                              | `portal-web:ff06a-c9516f9-20260729a` | `sha256:1553e6a46cdffe8bb029ec4265e5a19fcf68fcc8e099051d66f16e42a83562f7` |

The canonical policy identity is the local Docker image ID, which is also the image config digest
reported by `docker image inspect`. Mutable tags are convenience references and never authorize a
finding. Locally generated repository-digest labels are recorded for mapping only; no registry
digest or publication is claimed.

The deterministic reuse manifest identity is
`sha256:27bea0e7d7e6df6f3167c441f6a0bf39fbeaae5d4ec40bea05c336afb8eabcdb`.
Policy `2026-07-29.3` is recorded as
`sha256:eb5603f044325a86fe904e8b86d0a6c909b41ca15b360e2b25a9f62e07174cc3`;
the rebound exception register is
`sha256:927d11c6ee9f106cb76a924f090d412ab226555e72305a1a7a5dcd43dc4cd167`.
Trivy 0.70.0 scanned both exact IDs with vulnerability database identity
`sha256:3100c44c847cc7d5647d84ac39b6367b1229250dfce5d1712f3b348d9ed17ead`.
The immutable evidence version is `ff06a-2026-07-29.1`. Every first-party Critical/High exception
now additionally binds the binary package, installed version, `amd64` architecture, scanner
database identity, evidence version, and exact finding fingerprint.

Fresh exact-image probes reconfirmed:

- Debian `zlib1g` contains libz but no MiniZip component;
- Perl is 5.36.0 and the affected optional `Archive::Tar`, `Storable`, and
  `IO::Compress::Base` modules are absent;
- the architecture-specific Perl advisory condition does not apply to `amd64`;
- `infocmp` belongs to `ncurses-bin`, while source-package sibling findings do not ship it;
- the affected block-device parser is shipped by `libblkid1`, not its source-package siblings;
- `gzip`, `infocmp`, `libblkid`, `libacl`, and core Perl operations retain `unknown`
  reachability where complete indirect non-reachability is not proven.

All 45 findings still report `no_fix` in the selected scanner database. The existing expiry dates
remain unchanged: unknown/no-fix High dispositions expire on 2026-09-27, while verified
condition-absent false positives expire on 2027-01-25. No wildcard identity, blanket renewal,
tag-only exception, or fixed-available first-party Critical/High disposition was introduced.

OCI byte identity remains nondeterministic. The API application content was stable while local
exporter layer metadata changed. Next.js regenerated preview/server-action material in three
generated manifest files. This limitation is recorded rather than replaced with a source-derived
secret. FF-06 must reuse the exact handoff artifacts and must not rebuild before exact-image policy
verification.

### FF-06B scanner database evidence refresh

FF-06B preserved both FF-06A image IDs and selected the current, non-stale Trivy database identity
`sha256:f6e81714e94ef9becd5e44f25d7b9888e5b7c257303888229d9c4709b30b1a3e`.
The database was copied into a dedicated local evidence cache, pinned before scanning, and used
offline with `--skip-db-update` and `--skip-java-db-update`. Its identity remained unchanged before,
during, and after the API, Web, and central policy scans.

The refreshed evidence version is `ff06b-2026-07-29.1`. Every exact first-party disposition and
reachability decision now records two revisions: FF-06A is retained as `superseded`, while FF-06B is
the single `current` revision. Superseded evidence cannot satisfy current policy. The policy and
exception register still require the active database identity, evidence version, artifact, advisory,
binary package, installed version, architecture, and finding fingerprint to match exactly.

The normalized comparison found 343 unchanged first-party image findings: 10 Critical, 35 High,
115 Medium, 165 Low, and 18 Informational. There were no new or removed advisories, alias changes,
severity changes, package/version changes, fix-status changes, image secret findings, or image
misconfiguration findings. All 45 Critical/High findings still report `no_fix`; their reachability
conclusions and expiry dates remain unchanged. The earliest active first-party expiry is
2026-09-27, outside the 30-day checkpoint window.

The committed handoff uses schema `portal-final-artifacts/v2` and records database timestamps,
per-artifact normalized report identities, the combined finding-set identity, the unchanged-content
differential, reachability evidence identity, exception register identity, central policy identity,
and the exact offline scan flags. The central exact-image policy reported 1,340 findings across the
two frozen first-party images and three immutable vendor images with zero blocking findings.
This refresh is scanner provenance, not a vulnerability-free, production-ready, or byte-identical
OCI claim. OCI byte determinism remains `NOT VERIFIED`.

## CI execution

The pull-request/main CI workflow runs:

```text
tracked/index snapshot
  → Semgrep first-party source
  → OSV immutable Portal locks
  → Gitleaks tracked snapshot
  → zizmor workflows
  → centralized policy
  → sanitized fast.json
```

After the existing reproducible image build, the container job runs Trivy against the exact API and
Web image IDs plus the three Portal vendor digests. It also runs supplemental Docker configuration
checks; the S06-04 verifier remains authoritative.

The scheduled workflow runs the full tracked scan and a separate complete Git-history Gitleaks scan.
History scanning is not on every pull request. SARIF transport is deliberately optional and is not
required for enforcement.

Suggested retention is 14 days for pull-request reports, 30 days for main/scheduled reports, and
90 days for future release reports. Raw secret output is never retained.

## Deterministic outcomes

| Exit | Meaning                                  |
| ---: | ---------------------------------------- |
|    0 | Policy passed                            |
|   10 | Blocking finding                         |
|   20 | Scanner execution failure                |
|   21 | Stale mandatory scanner database/ruleset |
|   22 | Scan input or artifact identity mismatch |
|   23 | Invalid baseline or expired exception    |
|   24 | Unsafe report or redaction failure       |
|   25 | Scanner version/checksum mismatch        |

“No blocking findings” does not mean “no vulnerabilities.” Scanner absence, zero parsed packages,
invalid JSON, stale mandatory data, or inability to execute is a failure rather than a zero-finding
result.

## Incident boundary

If a potential credential is reported, stop unsafe output and inspect only the sanitized identity.
If confirmed, revoke and rotate it through a separately authorized response. Do not baseline the
credential, commit raw evidence, upload raw reports, or rewrite Git history during a scan.

Further dependency or image remediation remains separate from scanner governance. Preserve the
finding identity, establish reachability and fix status, then authorize the smallest dependency or
image change through normal review.

## Rollback

S06-05 is isolated tooling and CI configuration. Reverting its cohesive commit restores S06-04
runtime behavior without a schema, dependency, image, or data rollback. If one scanner is unstable,
isolate that integration and preserve sanitized failure evidence; do not disable every scanner or
weaken policy globally.

Never alter PostgreSQL state or the intentional audit dead letter while diagnosing scanning.

## Limitations and deferred work

- Semgrep CE has limited cross-file and framework-aware analysis.
- Advisory databases can be incomplete or delayed; dependency reachability may need manual review.
- Vendor findings depend on upstream remediation.
- Root data-platform Python dependencies do not have an immutable lock, so complete dependency
  scanning is not claimed for that surface.
- GitHub Code Security/SARIF availability is environment-dependent.
- Continuous registry/production monitoring and automated remediation are not implemented.
- SBOM, VEX, signing, provenance, release attestations, and SLSA claims are deferred.
- Production callback/abuse policy, production workload identity, a production secret provider, and
  staging/production security-runtime authorization remain deferred.

Accurate maturity statement:

> First-party Portal fixed-available Critical/High image vulnerabilities remediated and remaining
> exact-image findings dispositioned with bounded evidence; supply-chain attestation and production
> security authorization remain pending.
