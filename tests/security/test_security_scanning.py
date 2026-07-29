"""Deterministic tests for the S06-05 security policy engine."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_scanner() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "security" / "scan.py"
    spec = importlib.util.spec_from_file_location("portal_security_scan", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scanner() -> ModuleType:
    return load_scanner()


def toolchain_fixture() -> dict[str, Any]:
    scanners: dict[str, Any] = {}
    for name in ("semgrep", "osv", "gitleaks", "trivy", "zizmor"):
        scanners[name] = {
            "version": "1.2.3",
            "image": f"registry.example/{name}:1.2.3@sha256:" + ("a" * 64),
            "expected_version_pattern": r"\b1\.2\.3\b",
        }
    return {"schema_version": "portal-security-toolchain/v1", "scanners": scanners}


def baseline_fixture(*findings: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "portal-security-baseline/v1",
        "policy_version": "test-policy",
        "generated_from_commit": "a" * 40,
        "findings": list(findings),
    }


def baseline_finding(fingerprint: str) -> dict[str, Any]:
    return {
        "scanner": "semgrep",
        "scanner_version": "1.2.3",
        "rule_or_vulnerability_id": "rule",
        "normalized_aliases": [],
        "package": "",
        "package_version": "",
        "ecosystem": "",
        "asset_identity": "src/example.py",
        "repository_path": "src/example.py",
        "safe_fingerprint": fingerprint,
        "first_seen_commit": "b" * 40,
        "policy_version": "test-policy",
    }


def exception_fixture(
    fingerprint: str,
    *,
    identifier: str = "SCN-001",
    expiry: str = "2026-08-01",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "scanner": "semgrep",
        "finding_identifier": "rule",
        "safe_fingerprint": fingerprint,
        "asset": "src/example.py",
        "scope": "first_party_runtime",
        "severity": "high",
        "exploitability": "reachable",
        "fix_status": "no_fix",
        "reason": "Bounded compatibility exception.",
        "compensating_control": "Repository review and deterministic regression gate.",
        "owner": "repository-maintainers",
        "approved_by": "security-review",
        "created_date": "2026-07-01",
        "expiry_date": expiry,
        "review_trigger": "Scanner rule or affected asset changes.",
        "removal_criteria": "The finding is remediated or withdrawn.",
        "evidence_link": "docs/portal/security-scanning.md",
    }


def finding(scanner: ModuleType, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "scanner": "semgrep",
        "scanner_version": "1.2.3",
        "database_identity": "rules-v1",
        "identifier": "rule",
        "source": "c" * 40,
        "asset_type": "source",
        "asset_identity": "src/example.py",
        "repository_path": "src/example.py",
        "severity": "high",
        "exploitability": "reachable",
        "fix_status": "mitigation_available",
        "scope": "first_party_runtime",
    }
    defaults.update(overrides)
    return scanner.make_finding(**defaults)


def evidence_revisions(database: str, version: str) -> list[dict[str, str]]:
    return [
        {
            "evidence_version": version,
            "scanner_database_identity": database,
            "analysis_date": "2026-07-29",
            "status": "current",
        }
    ]


def test_scanner_toolchain_requires_all_immutable_identities(scanner: ModuleType) -> None:
    validated = scanner.validate_toolchain(toolchain_fixture(), verify_files=False)
    assert set(validated) == {"semgrep", "osv", "gitleaks", "trivy", "zizmor"}

    invalid = toolchain_fixture()
    invalid["scanners"]["trivy"]["image"] = "aquasec/trivy:latest"
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.validate_toolchain(invalid, verify_files=False)
    assert failure.value.exit_code == scanner.EXIT_TOOL_IDENTITY


def test_ruleset_checksum_mismatch_fails_closed(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scanning_root = tmp_path / "security" / "scanning"
    scanning_root.mkdir(parents=True)
    rules = scanning_root / "semgrep.yml"
    rules.write_text("rules: []\n", encoding="utf-8")
    data = toolchain_fixture()
    data["scanners"]["semgrep"].update(
        {
            "ruleset": "security/scanning/semgrep.yml",
            "ruleset_sha256": "sha256:" + ("0" * 64),
        }
    )
    monkeypatch.setattr(scanner, "REPOSITORY_ROOT", tmp_path)
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.validate_toolchain(data)
    assert failure.value.exit_code == scanner.EXIT_TOOL_IDENTITY


def test_scanner_execution_failure_is_not_a_clean_scan(scanner: ModuleType) -> None:
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner._run(
            [sys.executable, "-c", "raise SystemExit(7)"],
            safe_name="synthetic scanner",
        )
    assert failure.value.exit_code == scanner.EXIT_SCANNER_FAILURE


def test_scanner_timeout_fails_closed(scanner: ModuleType) -> None:
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner._run(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            safe_name="synthetic slow scanner",
            timeout_seconds=0.01,
        )
    assert failure.value.exit_code == scanner.EXIT_SCANNER_FAILURE


def test_scanner_temporary_storage_limit_is_bounded_and_validated(
    scanner: ModuleType,
) -> None:
    command = scanner._docker_base(network=False, temporary_storage_size="4g")
    assert "/tmp:rw,nosuid,nodev,noexec,size=4g,mode=1777" in command
    with pytest.raises(scanner.ScanFailure):
        scanner._docker_base(network=False, temporary_storage_size="unbounded")


def test_no_packages_found_is_a_dependency_scanner_failure(scanner: ModuleType) -> None:
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.parse_osv(
            {"results": []},
            version="2.3.8",
            database_identity="osv.dev-live-api",
            source="a" * 40,
            lock_path="apps/portal-api/requirements.lock",
        )
    assert failure.value.exit_code == scanner.EXIT_SCANNER_FAILURE


def test_stale_trivy_database_fails_closed(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = scanner.DockerScannerRunner(
        snapshot=tmp_path,
        output=tmp_path,
        scanners={
            "trivy": {
                "version": "0.70.0",
                "image": "aquasec/trivy:0.70.0@sha256:" + ("a" * 64),
                "database_max_age_hours": 1,
            }
        },
        source="a" * 40,
    )
    payload = json.dumps({"VulnerabilityDB": {"UpdatedAt": "2026-01-01T00:00:00Z"}})
    monkeypatch.setattr(scanner, "_run", lambda *args, **kwargs: (payload, "", 0))
    with pytest.raises(scanner.ScanFailure) as failure:
        runner._trivy_database_identity(tmp_path)
    assert failure.value.exit_code == scanner.EXIT_STALE_DATABASE


def test_missing_lock_input_is_an_identity_mismatch(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = scanner.DockerScannerRunner(
        snapshot=tmp_path,
        output=tmp_path,
        scanners={
            "osv": {
                "version": "2.3.8",
                "image": "google/osv:2.3.8@sha256:" + ("a" * 64),
                "database_identity": "osv.dev-live-api",
            }
        },
        source="a" * 40,
    )
    monkeypatch.setattr(runner, "validate_version", lambda _name: None)
    with pytest.raises(scanner.ScanFailure) as failure:
        runner.osv()
    assert failure.value.exit_code == scanner.EXIT_INPUT_MISMATCH


def test_invalid_image_identity_fails_closed(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scanner, "_run", lambda *args, **kwargs: ("mutable-tag", "", 0))
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.DockerScannerRunner._image_id("example:local")
    assert failure.value.exit_code == scanner.EXIT_INPUT_MISMATCH


def test_semgrep_finding_is_normalized_without_raw_context(scanner: ModuleType) -> None:
    findings = scanner.parse_semgrep(
        {
            "results": [
                {
                    "check_id": "portal.python.os-system",
                    "path": "apps/portal-api/app/example.py",
                    "extra": {
                        "severity": "ERROR",
                        "message": "scanner-native source context is discarded",
                        "metadata": {"exploitability": "likely_reachable"},
                    },
                }
            ]
        },
        version="1.164.0",
        database_identity="sha256:" + ("a" * 64),
        source="b" * 40,
    )
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].message == "semgrep reported portal.python.os-system"
    assert "context" not in scanner.asdict(findings[0])


def test_osv_aliases_are_sorted_and_deduplicated(scanner: ModuleType) -> None:
    payload = {
        "results": [
            {
                "packages": [
                    {
                        "package": {"name": "example", "version": "1.0", "ecosystem": "PyPI"},
                        "vulnerabilities": [
                            {
                                "id": "OSV-1",
                                "aliases": ["CVE-2", "CVE-1", "CVE-1"],
                                "database_specific": {"severity": "HIGH"},
                            }
                        ],
                    }
                ]
            }
        ]
    }
    result = scanner.parse_osv(
        payload,
        version="2.3.8",
        database_identity="osv.dev-live-api",
        source="a" * 40,
        lock_path="apps/portal-api/requirements.lock",
    )
    assert result[0].rule_or_vulnerability_id == "CVE-1"
    assert result[0].aliases == ("CVE-2", "OSV-1")


def test_osv_aliased_records_are_coalesced_and_use_group_severity(
    scanner: ModuleType,
) -> None:
    payload = {
        "groups": [
            {
                "ids": ["GHSA-example"],
                "aliases": ["CVE-2026-0001", "PYSEC-2026-1"],
                "max_severity": "8.2",
            }
        ],
        "results": [
            {
                "packages": [
                    {
                        "package": {
                            "name": "example",
                            "version": "1.0",
                            "ecosystem": "PyPI",
                        },
                        "vulnerabilities": [
                            {
                                "id": "GHSA-example",
                                "aliases": ["CVE-2026-0001"],
                                "affected": [{"ranges": [{"events": [{"fixed": "1.1"}]}]}],
                            },
                            {
                                "id": "PYSEC-2026-1",
                                "aliases": ["CVE-2026-0001"],
                            },
                        ],
                    }
                ]
            }
        ],
    }
    result = scanner.parse_osv(
        payload,
        version="2.3.8",
        database_identity="osv.dev-live-api",
        source="a" * 40,
        lock_path="apps/portal-api/requirements.lock",
    )
    assert len(result) == 1
    assert result[0].rule_or_vulnerability_id == "CVE-2026-0001"
    assert result[0].aliases == ("GHSA-example", "PYSEC-2026-1")
    assert result[0].severity == "high"
    assert result[0].fix_status == "fixed_available"


def test_dependency_scope_override_is_exact_and_development_only(
    scanner: ModuleType,
) -> None:
    vulnerable = scanner.make_finding(
        scanner="osv",
        scanner_version="2.3.8",
        database_identity="osv.dev-live-api",
        identifier="GHSA-example",
        source="a" * 40,
        asset_type="dependency",
        repository_path="pnpm-lock.yaml",
        asset_identity="pnpm-lock.yaml",
        package="vite",
        package_version="8.0.2",
        severity="high",
    )
    result = scanner.apply_dependency_scope_overrides(
        [vulnerable],
        [
            {
                "lock_path": "pnpm-lock.yaml",
                "package": "vite",
                "package_version": "8.0.2",
                "scope": "first_party_development",
                "exploitability": "development_only",
                "evidence": "pnpm production graph excludes vite",
            }
        ],
    )
    assert result[0].scope == "first_party_development"
    assert result[0].exploitability == "development_only"

    with pytest.raises(scanner.ScanFailure):
        scanner.apply_dependency_scope_overrides(
            [vulnerable],
            [
                {
                    "lock_path": "pnpm-lock.yaml",
                    "package": "vite",
                    "package_version": "8.0.2",
                    "scope": "first_party_runtime",
                    "exploitability": "reachable",
                    "evidence": "unsupported widening",
                }
            ],
        )


def test_image_reachability_override_requires_exact_complete_evidence(
    scanner: ModuleType,
) -> None:
    image = "sha256:" + ("a" * 64)
    vulnerable = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity="sha256:" + ("b" * 64),
        identifier="CVE-2026-0001",
        source="c" * 40,
        asset_type="container_image",
        asset_identity=image,
        repository_path="",
        package="example-package",
        package_version="1.2.3",
        severity="critical",
        exploitability="unknown",
        fix_status="no_fix",
    )
    override = {
        "asset_identities": [image],
        "vulnerability_id": "CVE-2026-0001",
        "packages": ["example-package"],
        "package_version": "1.2.3",
        "architectures": ["amd64"],
        "exploitability": "affected_condition_absent",
        "evidence": "Exact image inspection proves the affected component is absent.",
        "evidence_source": "Exact package and file inspection.",
        "analysis_date": "2026-07-29",
        "review_owner": "portal-maintainers",
        "expiry_or_removal_trigger": "Revalidate when the image or package changes.",
        "scanner_database_identity": "sha256:" + ("b" * 64),
        "evidence_version": "ff06a-test.1",
        "evidence_revisions": evidence_revisions(
            "sha256:" + ("b" * 64),
            "ff06a-test.1",
        ),
    }

    result = scanner.apply_image_reachability_overrides(
        [vulnerable],
        [override],
        artifact_architectures={image: "amd64"},
    )
    assert result[0].exploitability == "affected_condition_absent"

    with pytest.raises(scanner.ScanFailure):
        scanner.apply_image_reachability_overrides(
            [vulnerable],
            [],
            artifact_architectures={image: "amd64"},
        )

    unused = dict(override)
    unused["package_version"] = "9.9.9"
    with pytest.raises(scanner.ScanFailure):
        scanner.apply_image_reachability_overrides(
            [],
            [unused],
            artifact_architectures={image: "amd64"},
        )

    with pytest.raises(scanner.ScanFailure):
        scanner.apply_image_reachability_overrides(
            [vulnerable],
            [override, override],
            artifact_architectures={image: "amd64"},
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("asset_identities", ["sha256:" + ("d" * 64)]),
        ("vulnerability_id", "CVE-2026-9999"),
        ("packages", ["different-package"]),
        ("package_version", "9.9.9"),
        ("architectures", ["arm64"]),
        ("scanner_database_identity", "sha256:" + ("e" * 64)),
    ],
)
def test_image_evidence_is_rejected_for_every_exact_key_mismatch(
    scanner: ModuleType,
    field: str,
    replacement: Any,
) -> None:
    image = "sha256:" + ("a" * 64)
    database = "sha256:" + ("b" * 64)
    vulnerable = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity=database,
        identifier="CVE-2026-0001",
        source="c" * 40,
        asset_type="container_image",
        asset_identity=image,
        package="example-package",
        package_version="1.2.3",
        severity="high",
        exploitability="unknown",
        fix_status="no_fix",
    )
    override = {
        "asset_identities": [image],
        "vulnerability_id": "CVE-2026-0001",
        "packages": ["example-package"],
        "package_version": "1.2.3",
        "architectures": ["amd64"],
        "exploitability": "unknown",
        "evidence": "Indirect reachability cannot be completely excluded.",
        "evidence_source": "Exact image inspection.",
        "analysis_date": "2026-07-29",
        "review_owner": "portal-maintainers",
        "expiry_or_removal_trigger": "Revalidate when any exact key changes.",
        "scanner_database_identity": database,
        "evidence_version": "ff06a-test.1",
        "evidence_revisions": evidence_revisions(database, "ff06a-test.1"),
    }
    override[field] = replacement

    with pytest.raises(scanner.ScanFailure):
        scanner.apply_image_reachability_overrides(
            [vulnerable],
            [override],
            artifact_architectures={image: "amd64"},
        )


def test_image_evidence_rejects_missing_fields_wildcards_and_ungoverned_architecture(
    scanner: ModuleType,
) -> None:
    image = "sha256:" + ("a" * 64)
    vulnerable = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity="sha256:" + ("b" * 64),
        identifier="CVE-2026-0001",
        source="c" * 40,
        asset_type="container_image",
        asset_identity=image,
        package="example-package",
        package_version="1.2.3",
        severity="high",
        exploitability="unknown",
        fix_status="no_fix",
    )
    override = {
        "asset_identities": [image],
        "vulnerability_id": "CVE-2026-0001",
        "packages": ["example-package"],
        "package_version": "1.2.3",
        "architectures": ["amd64"],
        "exploitability": "unknown",
        "evidence": "Indirect reachability cannot be completely excluded.",
        "evidence_source": "Exact image inspection.",
        "analysis_date": "2026-07-29",
        "review_owner": "portal-maintainers",
        "expiry_or_removal_trigger": "Revalidate when any exact key changes.",
        "scanner_database_identity": "sha256:" + ("b" * 64),
        "evidence_version": "ff06a-test.1",
        "evidence_revisions": evidence_revisions(
            "sha256:" + ("b" * 64),
            "ff06a-test.1",
        ),
    }

    missing = dict(override)
    del missing["evidence_source"]
    wildcard = dict(override)
    wildcard["packages"] = ["*"]
    for invalid in (missing, wildcard):
        with pytest.raises(scanner.ScanFailure):
            scanner.apply_image_reachability_overrides(
                [vulnerable],
                [invalid],
                artifact_architectures={image: "amd64"},
            )
    with pytest.raises(scanner.ScanFailure, match="architecture is not governed"):
        scanner.apply_image_reachability_overrides(
            [vulnerable],
            [override],
            artifact_architectures={},
        )


def test_final_artifact_handoff_binds_tags_ids_source_and_architecture(
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = REPOSITORY_ROOT / "security" / "scanning" / "final-artifacts.json"
    value = json.loads(handoff.read_text(encoding="utf-8"))
    expected = {item["name"]: (item["local_tag"], item["image_id"]) for item in value["artifacts"]}
    source = value["source_commit"]
    monkeypatch.setattr(scanner, "REPOSITORY_ROOT", REPOSITORY_ROOT)

    architectures, evidence_version, database_identity = scanner.validate_final_artifact_handoff(
        handoff,
        source=source,
        expected_images=expected,
    )

    assert set(architectures.values()) == {"amd64"}
    assert evidence_version == "ff06b-2026-07-29.1"
    assert database_identity.startswith("sha256:")

    changed = dict(expected)
    api_tag, _api_id = changed["portal-api"]
    changed["portal-api"] = (api_tag, "sha256:" + ("f" * 64))
    with pytest.raises(scanner.ScanFailure, match="tag or image identity mismatch"):
        scanner.validate_final_artifact_handoff(
            handoff,
            source=source,
            expected_images=changed,
        )


def test_evidence_revisions_require_one_exact_current_revision(scanner: ModuleType) -> None:
    previous_database = "sha256:" + ("a" * 64)
    current_database = "sha256:" + ("b" * 64)
    revisions = [
        {
            "evidence_version": "ff06a-test.1",
            "scanner_database_identity": previous_database,
            "analysis_date": "2026-07-28",
            "status": "superseded",
        },
        {
            "evidence_version": "ff06b-test.1",
            "scanner_database_identity": current_database,
            "analysis_date": "2026-07-29",
            "status": "current",
        },
    ]

    validated = scanner._validate_evidence_revisions(
        revisions,
        current_database_identity=current_database,
        current_evidence_version="ff06b-test.1",
    )
    assert len(validated) == 2

    duplicate = [dict(revisions[1]), dict(revisions[1])]
    wildcard = [dict(revisions[1], scanner_database_identity="*")]
    superseded_only = [dict(revisions[1], status="superseded")]
    two_current = [dict(revisions[0], status="current"), dict(revisions[1])]
    for invalid in (duplicate, wildcard, superseded_only, two_current):
        with pytest.raises(scanner.ScanFailure):
            scanner._validate_evidence_revisions(
                invalid,
                current_database_identity=current_database,
                current_evidence_version="ff06b-test.1",
            )

    with pytest.raises(scanner.ScanFailure, match="does not match active"):
        scanner._validate_evidence_revisions(
            revisions,
            current_database_identity=previous_database,
            current_evidence_version="ff06a-test.1",
        )


def test_finding_content_identity_is_stable_across_exact_database_refresh(
    scanner: ModuleType,
) -> None:
    image = "sha256:" + ("a" * 64)
    previous = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity="sha256:" + ("b" * 64),
        identifier="CVE-2026-0001",
        asset_type="image_vulnerability",
        asset_identity=image,
        package="example-package",
        package_version="1.2.3",
        repository_path="",
        severity="high",
        exploitability="unknown",
        fix_status="no_fix",
        scope="first_party_runtime",
        source="c" * 40,
    )
    current = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity="sha256:" + ("c" * 64),
        identifier="CVE-2026-0001",
        asset_type="image_vulnerability",
        asset_identity=image,
        package="example-package",
        package_version="1.2.3",
        repository_path="",
        severity="high",
        exploitability="unknown",
        fix_status="no_fix",
        scope="first_party_runtime",
        source="c" * 40,
    )

    assert scanner._image_finding_identity(
        [previous],
        include_database=False,
    ) == scanner._image_finding_identity([current], include_database=False)
    assert scanner._image_finding_identity(
        [previous],
        include_database=True,
    ) != scanner._image_finding_identity([current], include_database=True)


def test_pinned_trivy_database_fails_closed_on_wrong_or_changed_identity(
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = "sha256:" + ("a" * 64)
    changed = "sha256:" + ("b" * 64)
    cache = tmp_path / "trivy"
    cache.mkdir()
    runner = scanner.DockerScannerRunner(
        snapshot=tmp_path,
        output=tmp_path,
        scanners={},
        source="c" * 40,
        trivy_cache=cache,
    )
    monkeypatch.setattr(runner, "_trivy_database_identity", lambda _cache: expected)
    runner.pin_trivy_database(expected)
    assert runner._verify_pinned_trivy_database() == expected

    monkeypatch.setattr(runner, "_trivy_database_identity", lambda _cache: changed)
    with pytest.raises(scanner.ScanFailure, match="changed after selection"):
        runner._verify_pinned_trivy_database()

    unpinned = scanner.DockerScannerRunner(
        snapshot=tmp_path,
        output=tmp_path,
        scanners={},
        source="c" * 40,
        trivy_cache=cache,
    )
    monkeypatch.setattr(unpinned, "_trivy_database_identity", lambda _cache: changed)
    with pytest.raises(scanner.ScanFailure, match="identity mismatch"):
        unpinned.pin_trivy_database(expected)


def test_committed_scanner_refresh_is_exact_current_and_non_wildcard(
    scanner: ModuleType,
) -> None:
    root = REPOSITORY_ROOT / "security" / "scanning"
    handoff = scanner._json(root / "final-artifacts.json")
    policy = scanner._json(root / "policy.json")
    exceptions = scanner._json(root / "exceptions.json")
    database = handoff["scanner"]["database_identity"]
    evidence_version = handoff["evidence_version"]

    assert handoff["schema_version"] == "portal-final-artifacts/v2"
    assert handoff["scanner_database"]["required_flags"] == [
        "--skip-db-update",
        "--skip-java-db-update",
    ]
    assert handoff["finding_differential"]["finding_inventory_changed"] is False
    assert (
        handoff["finding_differential"]["scanner_metadata_only_changes"]
        == handoff["finding_differential"]["current_finding_count"]
        == 343
    )

    governed = [
        item
        for item in exceptions["exceptions"]
        if item.get("scanner") == "trivy" and item.get("scope") == "first_party_runtime"
    ]
    assert len(governed) == 45
    for item in [*governed, *policy["image_reachability_overrides"]]:
        assert item["scanner_database_identity"] == database
        assert item["evidence_version"] == evidence_version
        assert "*" not in json.dumps(item["evidence_revisions"])
        assert len(item["evidence_revisions"]) == 2
        assert sum(revision["status"] == "current" for revision in item["evidence_revisions"]) == 1
        assert item["evidence_revisions"][-1] == {
            "evidence_version": evidence_version,
            "scanner_database_identity": database,
            "analysis_date": "2026-07-29",
            "status": "current",
        }


def test_zizmor_v1_locations_are_normalized(scanner: ModuleType) -> None:
    payload = [
        {
            "ident": "unpinned-uses",
            "ignored": False,
            "determinations": {"severity": "High"},
            "locations": [
                {
                    "symbolic": {
                        "key": {"Local": {"verbatim_path": "/src/.github/workflows/ci.yml"}}
                    },
                    "concrete": {"location": {"start_point": {"row": 42}}},
                }
            ],
        }
    ]
    result = scanner.parse_zizmor(
        payload,
        version="1.28.0",
        database_identity="zizmor-audits-v1",
        source="a" * 40,
    )
    assert len(result) == 1
    assert result[0].repository_path == ".github/workflows/ci.yml"
    assert result[0].asset_identity == ".github/workflows/ci.yml:42"
    assert result[0].severity == "high"


def test_trivy_image_secret_preserves_safe_internal_path(scanner: ModuleType) -> None:
    result = scanner.parse_trivy(
        {
            "Results": [
                {
                    "Target": "/etc/ssl/private/example.key",
                    "Secrets": [{"RuleID": "private-key", "Severity": "HIGH"}],
                }
            ]
        },
        version="0.70.0",
        database_identity="db-v1",
        source="a" * 40,
        asset_identity="sha256:" + ("b" * 64),
        scope="vendor_runtime",
    )
    assert len(result) == 1
    assert result[0].repository_path == "etc/ssl/private/example.key"
    assert result[0].asset_type == "potential_secret"


def test_new_baseline_advisory_and_resurfaced_classification(scanner: ModuleType) -> None:
    current = finding(scanner)
    baseline = {current.safe_fingerprint: baseline_finding(current.safe_fingerprint)}
    baseline_result = scanner.classify_findings(
        [current], baseline=baseline, exceptions={}, expired=set()
    )
    assert baseline_result[0].regression_state == "baseline"
    assert baseline_result[0].first_seen_commit == "b" * 40

    advisory_result = scanner.classify_findings(
        [current],
        baseline={},
        exceptions={},
        expired=set(),
        advisory_scanners=frozenset({"semgrep"}),
    )
    assert advisory_result[0].regression_state == "advisory_new"

    resurfaced = scanner.replace(current, regression_state="resurfaced")
    assert scanner.is_blocking(resurfaced, exceptions={})


def test_expired_exception_fails_and_marks_finding(scanner: ModuleType) -> None:
    current = finding(scanner)
    register = {
        "schema_version": "portal-security-exceptions/v1",
        "exceptions": [exception_fixture(current.safe_fingerprint, expiry="2026-07-02")],
    }
    exceptions, expired = scanner.validate_exceptions(register, today=date(2026, 7, 29))
    classified = scanner.classify_findings(
        [current], baseline={}, exceptions=exceptions, expired=expired
    )
    assert classified[0].regression_state == "expired_exception"
    assert scanner.is_blocking(classified[0], exceptions=exceptions)


@pytest.mark.parametrize("field", ["scanner", "finding_identifier", "asset"])
def test_wildcard_exception_is_rejected(scanner: ModuleType, field: str) -> None:
    item = exception_fixture("sha256:" + ("a" * 64))
    item[field] = "*"
    with pytest.raises(scanner.ScanFailure):
        scanner.validate_exceptions(
            {"schema_version": "portal-security-exceptions/v1", "exceptions": [item]}
        )


@pytest.mark.parametrize("field", ["owner", "approved_by", "expiry_date"])
def test_exception_requires_owner_approval_and_expiry(scanner: ModuleType, field: str) -> None:
    item = exception_fixture("sha256:" + ("a" * 64))
    item[field] = ""
    with pytest.raises(scanner.ScanFailure):
        scanner.validate_exceptions(
            {"schema_version": "portal-security-exceptions/v1", "exceptions": [item]}
        )


def test_exception_duration_policy_is_enforced(scanner: ModuleType) -> None:
    item = exception_fixture("sha256:" + ("a" * 64), expiry="2026-07-20")
    item["created_date"] = "2026-07-01"
    item["fix_status"] = "fixed_available"
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.validate_exceptions(
            {"schema_version": "portal-security-exceptions/v1", "exceptions": [item]},
            today=date(2026, 7, 2),
            maximum_days={"first_party_fixed_available_high": 14},
        )
    assert failure.value.exit_code == scanner.EXIT_INVALID_GOVERNANCE


def test_exception_metadata_must_match_finding(scanner: ModuleType) -> None:
    current = finding(scanner)
    item = exception_fixture(current.safe_fingerprint)
    item["finding_identifier"] = "different-rule"
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.is_blocking(
            current,
            exceptions={current.safe_fingerprint: item},
        )
    assert failure.value.exit_code == scanner.EXIT_INVALID_GOVERNANCE


def test_confirmed_secret_and_known_exploited_are_non_suppressible(
    scanner: ModuleType,
) -> None:
    secret = finding(scanner, asset_type="confirmed_secret")
    exploited = finding(scanner, exploitability="known_exploited")
    exceptions = {
        secret.safe_fingerprint: exception_fixture(secret.safe_fingerprint),
        exploited.safe_fingerprint: exception_fixture(
            exploited.safe_fingerprint, identifier="SCN-002"
        ),
    }
    assert scanner.is_blocking(secret, exceptions=exceptions)
    assert scanner.is_blocking(exploited, exceptions=exceptions)


def test_first_party_vendor_and_development_policy(scanner: ModuleType) -> None:
    runtime = finding(scanner)
    vendor = finding(scanner, scope="vendor_runtime")
    development = finding(
        scanner,
        scope="first_party_development",
        exploitability="development_only",
    )
    assert scanner.is_blocking(runtime, exceptions={})
    assert scanner.is_blocking(vendor, exceptions={})
    assert not scanner.is_blocking(development, exceptions={})


def test_baseline_first_party_high_still_requires_exception(scanner: ModuleType) -> None:
    current = scanner.replace(
        finding(scanner, fix_status="no_fix"),
        regression_state="baseline",
    )
    assert scanner.is_blocking(current, exceptions={})


def test_redaction_removes_credentials_and_machine_paths(scanner: ModuleType) -> None:
    rendered = scanner.redact_text(
        "Bearer example-not-real-value-12345 at C:\\Users\\operator\\project "
        "and postgresql://user:value@example.invalid/db"
    )
    assert "example-not-real" not in rendered
    assert "operator" not in rendered
    assert "user:value" not in rendered


def test_unsafe_raw_secret_report_is_rejected(scanner: ModuleType) -> None:
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.assert_safe_report({"raw": "scanner-native match"})
    assert failure.value.exit_code == scanner.EXIT_UNSAFE_REPORT


@pytest.mark.parametrize(
    "path",
    [
        "C:\\Users\\operator\\file.py",
        "/home/operator/file.py",
        "../outside.py",
    ],
)
def test_machine_specific_or_escaping_paths_are_rejected(scanner: ModuleType, path: str) -> None:
    with pytest.raises(scanner.ScanFailure) as failure:
        scanner.normalize_repository_path(path)
    assert failure.value.exit_code == scanner.EXIT_UNSAFE_REPORT


def test_index_snapshot_uses_only_git_index(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "build" / "security"
    monkeypatch.setattr(scanner, "REPOSITORY_ROOT", tmp_path)

    def fake_git(*arguments: str, **_kwargs: Any) -> str:
        prefix = next(
            value.removeprefix("--prefix=") for value in arguments if value.startswith("--prefix=")
        )
        target = Path(prefix) / "tracked.txt"
        target.write_text("tracked", encoding="utf-8")
        return ""

    monkeypatch.setattr(scanner, "git", fake_git)
    (tmp_path / "user-owned.txt").write_text("untouched", encoding="utf-8")
    snapshot = scanner.create_index_snapshot(output)
    assert (snapshot / "tracked.txt").read_text(encoding="utf-8") == "tracked"
    assert not (snapshot / "user-owned.txt").exists()


def test_history_snapshot_is_bare_and_excludes_worktree(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "build" / "security"
    monkeypatch.setattr(scanner, "REPOSITORY_ROOT", tmp_path)

    def fake_git(*arguments: str, **_kwargs: Any) -> str:
        assert arguments[:3] == ("clone", "--bare", "--no-hardlinks")
        target = Path(arguments[-1])
        (target / "objects").mkdir(parents=True)
        (target / "HEAD").write_text("ref: refs/heads/test\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(scanner, "git", fake_git)
    (tmp_path / "user-owned.txt").write_text("untouched", encoding="utf-8")
    history = scanner.create_history_snapshot(output)
    assert (history / "HEAD").is_file()
    assert not (history / "user-owned.txt").exists()


def test_exit_codes_are_stable_and_distinct(scanner: ModuleType) -> None:
    assert (
        scanner.EXIT_PASSED,
        scanner.EXIT_BLOCKING_FINDING,
        scanner.EXIT_SCANNER_FAILURE,
        scanner.EXIT_STALE_DATABASE,
        scanner.EXIT_INPUT_MISMATCH,
        scanner.EXIT_INVALID_GOVERNANCE,
        scanner.EXIT_UNSAFE_REPORT,
        scanner.EXIT_TOOL_IDENTITY,
    ) == (0, 10, 20, 21, 22, 23, 24, 25)


def test_portable_report_does_not_require_sarif(scanner: ModuleType) -> None:
    current = finding(scanner, severity="low")
    report = scanner.build_report(
        mode="fast",
        source="a" * 40,
        policy={"policy_version": "test"},
        baseline=baseline_fixture(),
        exceptions={"schema_version": "portal-security-exceptions/v1", "exceptions": []},
        findings=[current],
        executions=[],
        blocking=[],
        started_at=datetime.now(UTC),
    )
    assert report["result"] == "passed"
    assert "sarif" not in report


def test_invalid_baseline_and_duplicate_fingerprint_fail(scanner: ModuleType) -> None:
    fingerprint = "sha256:" + ("a" * 64)
    duplicate = baseline_fixture(baseline_finding(fingerprint), baseline_finding(fingerprint))
    with pytest.raises(scanner.ScanFailure):
        scanner.validate_baseline(duplicate, policy_version="test-policy")

    invalid = baseline_fixture()
    invalid["schema_version"] = "unsupported"
    with pytest.raises(scanner.ScanFailure):
        scanner.validate_baseline(invalid, policy_version="test-policy")


def test_false_positive_exception_is_bounded_and_authorizes_high_finding(
    scanner: ModuleType,
) -> None:
    current = finding(scanner)
    item = exception_fixture(current.safe_fingerprint)
    item["fix_status"] = "false_positive"
    register = {
        "schema_version": "portal-security-exceptions/v1",
        "exceptions": [item],
    }
    exceptions, expired = scanner.validate_exceptions(register, today=date(2026, 7, 29))
    assert not expired
    assert not scanner.is_blocking(current, exceptions=exceptions)


def test_first_party_image_exception_requires_exact_package_database_and_evidence(
    scanner: ModuleType,
) -> None:
    image = "sha256:" + ("a" * 64)
    database = "sha256:" + ("b" * 64)
    current = scanner.make_finding(
        scanner="trivy",
        scanner_version="0.70.0",
        database_identity=database,
        identifier="CVE-2026-0001",
        asset_type="container_image",
        asset_identity=image,
        package="example-package",
        package_version="1.2.3",
        repository_path="",
        exploitability="unknown",
        fix_status="no_fix",
        scope="first_party_runtime",
        severity="high",
        source="c" * 40,
    )
    item = {
        "id": "SCN-001",
        "scanner": "trivy",
        "finding_identifier": "CVE-2026-0001",
        "package": "example-package",
        "package_version": "1.2.3",
        "safe_fingerprint": current.safe_fingerprint,
        "asset": image,
        "architecture": "amd64",
        "scanner_database_identity": database,
        "evidence_version": "ff06a-test.1",
        "evidence_revisions": evidence_revisions(database, "ff06a-test.1"),
        "scope": "first_party_runtime",
        "severity": "high",
        "exploitability": "unknown",
        "fix_status": "no_fix",
        "reason": "Bounded exact-image exception.",
        "compensating_control": "Read-only non-root runtime.",
        "owner": "portal-maintainers",
        "approved_by": "security-review",
        "created_date": "2026-07-01",
        "expiry_date": "2026-08-30",
        "review_trigger": "Any exact identity changes.",
        "removal_criteria": "A fixed package is available.",
        "evidence_link": "docs/portal/security-scanning.md",
    }
    exceptions, expired = scanner.validate_exceptions(
        {
            "schema_version": "portal-security-exceptions/v1",
            "exceptions": [item],
        },
        today=date(2026, 7, 29),
    )
    assert not expired
    assert not scanner.is_blocking(
        current,
        exceptions=exceptions,
        artifact_architectures={image: "amd64"},
        evidence_version="ff06a-test.1",
    )

    mismatched = dict(item)
    mismatched["package_version"] = "9.9.9"
    with pytest.raises(scanner.ScanFailure, match="metadata mismatch"):
        scanner.is_blocking(
            current,
            exceptions={current.safe_fingerprint: mismatched},
            artifact_architectures={image: "amd64"},
            evidence_version="ff06a-test.1",
        )


def test_local_and_ci_policy_evaluation_is_equivalent(scanner: ModuleType) -> None:
    current = finding(scanner, severity="medium")
    local = scanner.is_blocking(current, exceptions={})
    ci = scanner.is_blocking(current, exceptions={})
    assert local is ci is False


def test_pull_request_range_is_bounded_and_local_mode_uses_index(scanner: ModuleType) -> None:
    base = "a" * 40
    head = "b" * 40
    assert scanner.pull_request_git_range("") is None
    assert scanner.pull_request_git_range(f"{base}..{head}") == f"{base}..{head}"
    with pytest.raises(scanner.ScanFailure) as captured:
        scanner.pull_request_git_range("--all")
    assert captured.value.exit_code == scanner.EXIT_INPUT_MISMATCH


def test_policy_configuration_files_are_internally_valid(scanner: ModuleType) -> None:
    toolchain = scanner._json(REPOSITORY_ROOT / "security" / "scanning" / "toolchain.json")
    policy = scanner._json(REPOSITORY_ROOT / "security" / "scanning" / "policy.json")
    baseline = scanner._json(REPOSITORY_ROOT / "security" / "scanning" / "baseline.json")
    exceptions = scanner._json(REPOSITORY_ROOT / "security" / "scanning" / "exceptions.json")

    scanner.validate_toolchain(toolchain)
    scanner.validate_baseline(baseline, policy_version=policy["policy_version"])
    indexed, expired = scanner.validate_exceptions(
        exceptions,
        today=date(2026, 7, 29),
        maximum_days=policy["exception_maximum_days"],
    )
    assert len(baseline["findings"]) == 89
    assert len(indexed) == 46
    assert sum(item["fix_status"] == "false_positive" for item in indexed.values()) == 36
    assert sum(item["fix_status"] == "no_fix" for item in indexed.values()) == 10
    assert not expired


def test_portal_vendor_image_inventory_requires_immutable_digests(scanner: ModuleType) -> None:
    images = scanner._portal_vendor_images(REPOSITORY_ROOT / "docker-compose.yml")
    assert len(images) == 3
    assert all("@sha256:" in image for image in images)


def test_artifact_manifest_must_match_source_and_exact_image_ids(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(scanner, "REPOSITORY_ROOT", tmp_path)
    manifest = tmp_path / "manifest.json"
    expected = {
        "portal-api": "sha256:" + ("a" * 64),
        "portal-web": "sha256:" + ("b" * 64),
    }
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "portal-artifact-build/v1",
                "repository": {"source_commit": "c" * 40},
                "images": [
                    {"name": name, "output": {"digest": digest}}
                    for name, digest in expected.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    identity = scanner.validate_artifact_manifest(
        manifest,
        source="c" * 40,
        expected_image_ids=expected,
    )
    assert identity.startswith("sha256:")

    with pytest.raises(scanner.ScanFailure):
        scanner.validate_artifact_manifest(
            manifest,
            source="d" * 40,
            expected_image_ids=expected,
        )


def test_artifact_manifest_allows_one_governance_only_child_commit(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_source = "c" * 40
    governance_source = "d" * 40
    expected = {
        "portal-api": "sha256:" + ("a" * 64),
        "portal-web": "sha256:" + ("b" * 64),
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "portal-artifact-build/v1",
                "repository": {"source_commit": artifact_source},
                "images": [
                    {"name": name, "output": {"digest": digest}}
                    for name, digest in expected.items()
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_git(*arguments: str, check: bool = True) -> str:
        del check
        if arguments == ("merge-base", artifact_source, governance_source):
            return artifact_source
        if arguments == ("diff", "--name-only", f"{artifact_source}..{governance_source}"):
            return "security/scanning/exceptions.json\nscripts/security/scan.py"
        raise AssertionError(arguments)

    monkeypatch.setattr(scanner, "git", fake_git)
    scanner.validate_artifact_manifest(
        manifest,
        source=governance_source,
        expected_image_ids=expected,
    )

    def unsafe_git(*arguments: str, check: bool = True) -> str:
        del check
        if arguments[0] == "merge-base":
            return artifact_source
        return "apps/portal-api/Dockerfile"

    monkeypatch.setattr(scanner, "git", unsafe_git)
    with pytest.raises(scanner.ScanFailure):
        scanner.validate_artifact_manifest(
            manifest,
            source=governance_source,
            expected_image_ids=expected,
        )
