"""Deterministic, read-only orchestration for Portal security scanners.

The scanner processes are pinned OCI images. Scanner-native output is captured in
memory, normalized, redacted, and discarded. Only the portable normalized report
is written below the ignored ``build/security`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
SCANNING_ROOT: Final = REPOSITORY_ROOT / "security" / "scanning"
TOOLCHAIN_PATH: Final = SCANNING_ROOT / "toolchain.json"
POLICY_PATH: Final = SCANNING_ROOT / "policy.json"
BASELINE_PATH: Final = SCANNING_ROOT / "baseline.json"
EXCEPTIONS_PATH: Final = SCANNING_ROOT / "exceptions.json"
DEFAULT_OUTPUT: Final = REPOSITORY_ROOT / "build" / "security"
IMAGE_REFERENCE: Final = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")
SHA256: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
COMMIT_RANGE: Final = re.compile(r"^[0-9a-f]{40}\.\.[0-9a-f]{40}$")
SCN_ID: Final = re.compile(r"^SCN-[0-9]{3}$")
WINDOWS_ABSOLUTE_PATH: Final = re.compile(r"(?i)(?:^|[\s\"'])([a-z]:[\\/][^\s\"']+)")
POSIX_HOME_PATH: Final = re.compile(r"(?:^|[\s\"'])(/(?:home|Users)/[^\s\"']+)")
CREDENTIAL_URL: Final = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@")
BEARER_VALUE: Final = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}={0,2}")
HIGH_ENTROPY_VALUE: Final = re.compile(r"\b[A-Za-z0-9+/_-]{48,}={0,2}\b")
FORBIDDEN_REPORT_KEYS: Final = {
    "authorization",
    "credential",
    "match",
    "password",
    "private_key",
    "raw",
    "secret_value",
    "token",
}
SEVERITIES: Final = ("critical", "high", "medium", "low", "informational")
EXPLOITABILITY: Final = (
    "known_exploited",
    "reachable",
    "likely_reachable",
    "unknown",
    "build_only",
    "development_only",
    "not_present",
)
FIX_STATUSES: Final = (
    "fixed_available",
    "mitigation_available",
    "no_fix",
    "disputed",
    "withdrawn",
    "false_positive",
)
SCOPES: Final = (
    "first_party_runtime",
    "first_party_development",
    "vendor_runtime",
    "test_fixture",
    "documentation",
    "generated_artifact",
    "github_workflow",
)
REGRESSION_STATES: Final = (
    "new",
    "baseline",
    "advisory_new",
    "resurfaced",
    "expired_exception",
    "resolved",
)

EXIT_PASSED: Final = 0
EXIT_BLOCKING_FINDING: Final = 10
EXIT_SCANNER_FAILURE: Final = 20
EXIT_STALE_DATABASE: Final = 21
EXIT_INPUT_MISMATCH: Final = 22
EXIT_INVALID_GOVERNANCE: Final = 23
EXIT_UNSAFE_REPORT: Final = 24
EXIT_TOOL_IDENTITY: Final = 25

ARTIFACT_GOVERNANCE_ONLY_PATHS: Final = frozenset(
    {
        "docs/portal/security-scanning.md",
        "scripts/security/scan.py",
        "security/scanning/baseline.json",
        "security/scanning/exceptions.json",
        "security/scanning/policy.json",
        "security/scanning/schemas/finding.schema.json",
        "tests/security/test_security_scanning.py",
    }
)


class ScanFailure(RuntimeError):
    """Safe scanner failure carrying the public deterministic exit code."""

    def __init__(self, message: str, *, exit_code: int = EXIT_SCANNER_FAILURE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Finding:
    schema_version: str
    scanner: str
    scanner_version: str
    scanner_database_identity: str
    rule_or_vulnerability_id: str
    aliases: tuple[str, ...]
    asset_type: str
    asset_identity: str
    package: str
    package_version: str
    ecosystem: str
    repository_path: str
    safe_fingerprint: str
    severity: str
    exploitability: str
    fix_status: str
    scope: str
    regression_state: str
    first_seen_commit: str
    source_commit: str
    message: str
    references: tuple[str, ...]


@dataclass(frozen=True)
class ScannerExecution:
    scanner: str
    version: str
    immutable_image: str
    database_identity: str
    input_identity: str
    finding_count: int
    status: str = "completed"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScanFailure(
            f"invalid JSON configuration: {path.relative_to(REPOSITORY_ROOT).as_posix()}",
            exit_code=EXIT_INVALID_GOVERNANCE,
        ) from error
    if not isinstance(value, dict):
        raise ScanFailure(
            f"configuration root must be an object: {path.relative_to(REPOSITORY_ROOT).as_posix()}",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    return value


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def normalize_repository_path(value: str) -> str:
    candidate = value.replace("\\", "/").removeprefix("./")
    if candidate.startswith("/src/"):
        candidate = candidate.removeprefix("/src/")
    elif candidate == "/src":
        candidate = ""
    if re.match(r"^[A-Za-z]:/", candidate) or candidate.startswith("/"):
        raise ScanFailure("scanner returned an absolute path", exit_code=EXIT_UNSAFE_REPORT)
    path = PurePosixPath(candidate)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ScanFailure("scanner returned an unsafe path", exit_code=EXIT_UNSAFE_REPORT)
    return path.as_posix()


def redact_text(value: str) -> str:
    sanitized = CREDENTIAL_URL.sub("<redacted-url>@", value)
    sanitized = BEARER_VALUE.sub("Bearer <redacted>", sanitized)
    sanitized = WINDOWS_ABSOLUTE_PATH.sub(" <local-path>", sanitized)
    sanitized = POSIX_HOME_PATH.sub(" <local-path>", sanitized)
    sanitized = HIGH_ENTROPY_VALUE.sub("<redacted-value>", sanitized)
    return " ".join(sanitized.split())[:500]


def assert_safe_report(value: object, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_REPORT_KEYS:
                raise ScanFailure(
                    f"unsafe report field: {'.'.join((*path, str(key)))}",
                    exit_code=EXIT_UNSAFE_REPORT,
                )
            assert_safe_report(child, path=(*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_safe_report(child, path=(*path, str(index)))
    elif isinstance(value, str):
        if CREDENTIAL_URL.search(value) or BEARER_VALUE.search(value):
            raise ScanFailure("unsafe credential material in report", exit_code=EXIT_UNSAFE_REPORT)
        if WINDOWS_ABSOLUTE_PATH.search(value) or POSIX_HOME_PATH.search(value):
            raise ScanFailure("host-specific path in report", exit_code=EXIT_UNSAFE_REPORT)


def git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise ScanFailure("Git input discovery failed", exit_code=EXIT_INPUT_MISMATCH)
    return result.stdout.strip()


def source_commit() -> str:
    value = git("rev-parse", "HEAD")
    if not COMMIT_SHA.fullmatch(value):
        raise ScanFailure("invalid source commit identity", exit_code=EXIT_INPUT_MISMATCH)
    return value


def pull_request_git_range(value: str | None = None) -> str | None:
    """Return a validated PR commit range or select the staged/index scan locally."""
    candidate = value if value is not None else os.environ.get("PORTAL_SECURITY_GIT_RANGE", "")
    candidate = candidate.strip()
    if not candidate:
        return None
    if COMMIT_RANGE.fullmatch(candidate) is None:
        raise ScanFailure("invalid bounded Git range", exit_code=EXIT_INPUT_MISMATCH)
    return candidate


def _safe_output_root(output: Path) -> Path:
    resolved = output.resolve()
    build_root = (REPOSITORY_ROOT / "build").resolve()
    if resolved != build_root and build_root not in resolved.parents:
        raise ScanFailure("report output must remain below build/", exit_code=EXIT_INPUT_MISMATCH)
    return resolved


def create_index_snapshot(output: Path) -> Path:
    """Create a scanner input from the Git index without reading untracked content."""
    work_root = _safe_output_root(output) / "work"
    snapshot = work_root / "tracked"
    if snapshot.exists():
        resolved = snapshot.resolve()
        if work_root.resolve() not in resolved.parents:
            raise ScanFailure("unsafe snapshot path", exit_code=EXIT_INPUT_MISMATCH)
        shutil.rmtree(resolved)
    snapshot.mkdir(parents=True)
    prefix = snapshot.resolve().as_posix().rstrip("/") + "/"
    git("checkout-index", "--all", "--force", f"--prefix={prefix}")
    return snapshot


def create_history_snapshot(output: Path) -> Path:
    """Create a bare committed-history input without exposing the local worktree."""
    work_root = _safe_output_root(output) / "work"
    history = work_root / "history.git"
    if history.exists():
        resolved = history.resolve()
        if work_root.resolve() not in resolved.parents:
            raise ScanFailure("unsafe history path", exit_code=EXIT_INPUT_MISMATCH)
        shutil.rmtree(resolved)
    history.parent.mkdir(parents=True, exist_ok=True)
    git(
        "clone",
        "--bare",
        "--no-hardlinks",
        str(REPOSITORY_ROOT.resolve()),
        str(history.resolve()),
    )
    if not (history / "HEAD").is_file() or not (history / "objects").is_dir():
        raise ScanFailure("committed-history snapshot failed", exit_code=EXIT_INPUT_MISMATCH)
    return history


def validate_toolchain(
    toolchain: Mapping[str, Any],
    *,
    verify_files: bool = True,
) -> dict[str, Mapping[str, Any]]:
    if toolchain.get("schema_version") != "portal-security-toolchain/v1":
        raise ScanFailure("unsupported toolchain schema", exit_code=EXIT_INVALID_GOVERNANCE)
    scanners = toolchain.get("scanners")
    if not isinstance(scanners, dict) or set(scanners) != {
        "semgrep",
        "osv",
        "gitleaks",
        "trivy",
        "zizmor",
    }:
        raise ScanFailure("scanner toolchain is incomplete", exit_code=EXIT_INVALID_GOVERNANCE)
    validated: dict[str, Mapping[str, Any]] = {}
    for name in sorted(scanners):
        item = scanners[name]
        if not isinstance(item, dict):
            raise ScanFailure("invalid scanner identity", exit_code=EXIT_INVALID_GOVERNANCE)
        image = item.get("image")
        version = item.get("version")
        pattern = item.get("expected_version_pattern")
        if not all(isinstance(part, str) and part for part in (image, version, pattern)):
            raise ScanFailure("incomplete scanner identity", exit_code=EXIT_INVALID_GOVERNANCE)
        if not IMAGE_REFERENCE.fullmatch(image):
            raise ScanFailure(f"{name} image is not immutable", exit_code=EXIT_TOOL_IDENTITY)
        if verify_files and "ruleset" in item:
            ruleset = REPOSITORY_ROOT / str(item["ruleset"])
            expected = item.get("ruleset_sha256")
            if (
                not ruleset.is_file()
                or not isinstance(expected, str)
                or not SHA256.fullmatch(expected)
            ):
                raise ScanFailure(
                    f"{name} ruleset identity is invalid", exit_code=EXIT_TOOL_IDENTITY
                )
            if sha256_file(ruleset) != expected:
                raise ScanFailure(f"{name} ruleset checksum mismatch", exit_code=EXIT_TOOL_IDENTITY)
        validated[name] = item
    return validated


def validate_baseline(
    baseline: Mapping[str, Any],
    *,
    policy_version: str,
) -> dict[str, Mapping[str, Any]]:
    if baseline.get("schema_version") != "portal-security-baseline/v1":
        raise ScanFailure("unsupported baseline schema", exit_code=EXIT_INVALID_GOVERNANCE)
    if baseline.get("policy_version") != policy_version:
        raise ScanFailure("baseline policy version mismatch", exit_code=EXIT_INVALID_GOVERNANCE)
    generated = baseline.get("generated_from_commit")
    if not isinstance(generated, str) or not COMMIT_SHA.fullmatch(generated):
        raise ScanFailure("invalid baseline commit identity", exit_code=EXIT_INVALID_GOVERNANCE)
    findings = baseline.get("findings")
    if not isinstance(findings, list):
        raise ScanFailure("baseline findings must be a list", exit_code=EXIT_INVALID_GOVERNANCE)
    indexed: dict[str, Mapping[str, Any]] = {}
    required = {
        "scanner",
        "scanner_version",
        "rule_or_vulnerability_id",
        "normalized_aliases",
        "package",
        "package_version",
        "ecosystem",
        "asset_identity",
        "repository_path",
        "safe_fingerprint",
        "first_seen_commit",
        "policy_version",
    }
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != required:
            raise ScanFailure("invalid baseline finding", exit_code=EXIT_INVALID_GOVERNANCE)
        fingerprint = finding.get("safe_fingerprint")
        if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint):
            raise ScanFailure("invalid baseline fingerprint", exit_code=EXIT_INVALID_GOVERNANCE)
        if fingerprint in indexed:
            raise ScanFailure("duplicate baseline fingerprint", exit_code=EXIT_INVALID_GOVERNANCE)
        indexed[fingerprint] = finding
    return indexed


def validate_exceptions(
    register: Mapping[str, Any],
    *,
    today: date | None = None,
    maximum_days: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    if register.get("schema_version") != "portal-security-exceptions/v1":
        raise ScanFailure("unsupported exception schema", exit_code=EXIT_INVALID_GOVERNANCE)
    exceptions = register.get("exceptions")
    if not isinstance(exceptions, list):
        raise ScanFailure("exceptions must be a list", exit_code=EXIT_INVALID_GOVERNANCE)
    current = today or datetime.now(UTC).date()
    indexed: dict[str, Mapping[str, Any]] = {}
    expired: set[str] = set()
    seen_ids: set[str] = set()
    required = {
        "id",
        "scanner",
        "finding_identifier",
        "safe_fingerprint",
        "asset",
        "scope",
        "severity",
        "exploitability",
        "fix_status",
        "reason",
        "compensating_control",
        "owner",
        "approved_by",
        "created_date",
        "expiry_date",
        "review_trigger",
        "removal_criteria",
        "evidence_link",
    }
    for item in exceptions:
        if not isinstance(item, dict) or set(item) != required:
            raise ScanFailure("invalid SCN exception", exit_code=EXIT_INVALID_GOVERNANCE)
        identifier = item.get("id")
        fingerprint = item.get("safe_fingerprint")
        if not isinstance(identifier, str) or not SCN_ID.fullmatch(identifier):
            raise ScanFailure("invalid SCN exception ID", exit_code=EXIT_INVALID_GOVERNANCE)
        if identifier in seen_ids:
            raise ScanFailure("duplicate SCN exception ID", exit_code=EXIT_INVALID_GOVERNANCE)
        seen_ids.add(identifier)
        if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint):
            raise ScanFailure("invalid SCN fingerprint", exit_code=EXIT_INVALID_GOVERNANCE)
        for key in ("finding_identifier", "asset", "scanner"):
            value = item.get(key)
            if not isinstance(value, str) or not value or "*" in value or "?" in value:
                raise ScanFailure(
                    "wildcard or empty SCN exception",
                    exit_code=EXIT_INVALID_GOVERNANCE,
                )
        for key in (
            "reason",
            "compensating_control",
            "owner",
            "approved_by",
            "review_trigger",
            "removal_criteria",
            "evidence_link",
        ):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ScanFailure("incomplete SCN exception", exit_code=EXIT_INVALID_GOVERNANCE)
        try:
            created = date.fromisoformat(str(item["created_date"]))
            expiry = date.fromisoformat(str(item["expiry_date"]))
        except ValueError as error:
            raise ScanFailure(
                "invalid SCN exception date", exit_code=EXIT_INVALID_GOVERNANCE
            ) from error
        if expiry <= created:
            raise ScanFailure("invalid SCN exception lifetime", exit_code=EXIT_INVALID_GOVERNANCE)
        if maximum_days is not None:
            category = _exception_duration_category(item)
            maximum = maximum_days.get(category)
            if not isinstance(maximum, int) or maximum <= 0:
                raise ScanFailure(
                    "missing SCN exception duration policy",
                    exit_code=EXIT_INVALID_GOVERNANCE,
                )
            if (expiry - created).days > maximum:
                raise ScanFailure(
                    "SCN exception exceeds duration policy",
                    exit_code=EXIT_INVALID_GOVERNANCE,
                )
        if expiry < current:
            expired.add(fingerprint)
        indexed[fingerprint] = item
    return indexed, expired


def _exception_duration_category(item: Mapping[str, Any]) -> str:
    fix_status = str(item.get("fix_status") or "")
    scope = str(item.get("scope") or "")
    severity = str(item.get("severity") or "")
    if fix_status == "false_positive":
        return "false_positive"
    if fix_status in {"disputed", "withdrawn"}:
        return "disputed_or_withdrawn"
    if scope in {"first_party_development", "test_fixture", "github_workflow"}:
        return "development_or_build_only"
    if scope == "vendor_runtime" and severity in {"critical", "high"}:
        return "vendor_critical_high"
    if scope == "first_party_runtime" and severity == "critical":
        return "first_party_critical"
    if scope == "first_party_runtime" and severity == "high":
        if fix_status == "fixed_available":
            return "first_party_fixed_available_high"
        return "first_party_no_fix_high"
    raise ScanFailure(
        "SCN exception has no bounded duration category",
        exit_code=EXIT_INVALID_GOVERNANCE,
    )


def _severity(value: Any, *, default: str = "informational") -> str:
    text = str(value or "").lower().replace("error", "high").replace("warning", "medium")
    for severity in SEVERITIES:
        if severity in text:
            return severity
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score >= 9:
        return "critical"
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    if score > 0:
        return "low"
    return default


def _scope(path: str, *, development: bool = False) -> str:
    if path.startswith(".github/workflows/"):
        return "github_workflow"
    if path.startswith("docs/"):
        return "documentation"
    if "/tests/" in f"/{path}" or path.startswith("tests/") or "fixture" in path.lower():
        return "test_fixture"
    if development:
        return "first_party_development"
    return "first_party_runtime"


def _fingerprint(
    *,
    scanner: str,
    identifier: str,
    asset_identity: str,
    path: str,
    package: str,
    package_version: str,
) -> str:
    return canonical_digest(
        {
            "scanner": scanner,
            "identifier": identifier,
            "asset_identity": asset_identity,
            "path": path,
            "package": package,
            "package_version": package_version,
        }
    )


def make_finding(
    *,
    scanner: str,
    scanner_version: str,
    database_identity: str,
    identifier: str,
    source: str,
    asset_type: str,
    asset_identity: str,
    repository_path: str = "",
    aliases: Iterable[str] = (),
    package: str = "",
    package_version: str = "",
    ecosystem: str = "",
    severity: str = "informational",
    exploitability: str = "unknown",
    fix_status: str = "no_fix",
    scope: str = "first_party_runtime",
    references: Iterable[str] = (),
) -> Finding:
    path = normalize_repository_path(repository_path) if repository_path else ""
    normalized_aliases = tuple(sorted({str(value) for value in aliases if value}))
    normalized_references = tuple(
        sorted(
            {
                value
                for value in (redact_text(str(item)) for item in references)
                if value.startswith("https://")
            }
        )
    )
    if severity not in SEVERITIES:
        severity = "informational"
    if exploitability not in EXPLOITABILITY:
        exploitability = "unknown"
    if fix_status not in FIX_STATUSES:
        fix_status = "no_fix"
    if scope not in SCOPES:
        scope = "first_party_runtime"
    fingerprint = _fingerprint(
        scanner=scanner,
        identifier=identifier,
        asset_identity=asset_identity,
        path=path,
        package=package,
        package_version=package_version,
    )
    return Finding(
        schema_version="portal-security-finding/v1",
        scanner=scanner,
        scanner_version=scanner_version,
        scanner_database_identity=database_identity,
        rule_or_vulnerability_id=identifier,
        aliases=normalized_aliases,
        asset_type=asset_type,
        asset_identity=asset_identity,
        package=package,
        package_version=package_version,
        ecosystem=ecosystem,
        repository_path=path,
        safe_fingerprint=fingerprint,
        severity=severity,
        exploitability=exploitability,
        fix_status=fix_status,
        scope=scope,
        regression_state="new",
        first_seen_commit=source,
        source_commit=source,
        message=f"{scanner} reported {identifier}",
        references=normalized_references,
    )


def parse_semgrep(
    payload: Mapping[str, Any],
    *,
    version: str,
    database_identity: str,
    source: str,
) -> list[Finding]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ScanFailure("invalid Semgrep JSON", exit_code=EXIT_SCANNER_FAILURE)
    findings: list[Finding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        identifier = str(result.get("check_id") or "semgrep-unknown")
        path = normalize_repository_path(str(result.get("path") or ""))
        extra = result.get("extra") if isinstance(result.get("extra"), dict) else {}
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
        exploitability = str(metadata.get("exploitability") or "unknown")
        findings.append(
            make_finding(
                scanner="semgrep",
                scanner_version=version,
                database_identity=database_identity,
                identifier=identifier,
                source=source,
                asset_type="source",
                asset_identity=path,
                repository_path=path,
                severity=_severity(extra.get("severity")),
                exploitability=exploitability,
                fix_status="mitigation_available",
                scope=_scope(path),
            )
        )
    return findings


def _osv_severity(vulnerability: Mapping[str, Any]) -> str:
    database_specific = vulnerability.get("database_specific")
    if isinstance(database_specific, dict) and database_specific.get("severity"):
        return _severity(database_specific["severity"])
    severities = vulnerability.get("severity")
    if isinstance(severities, list):
        for item in severities:
            if isinstance(item, dict):
                score = str(item.get("score") or "")
                match = re.search(r"(?:CVSS:[^/]+/)?(?:AV:[^/]+/.*)?/([0-9]+\.[0-9]+)$", score)
                if match:
                    return _severity(match.group(1))
                numeric = re.search(r"\b([0-9]+\.[0-9]+)\b", score)
                if numeric:
                    return _severity(numeric.group(1))
    return "informational"


def _highest_severity(*values: str) -> str:
    ranked = {value: index for index, value in enumerate(SEVERITIES)}
    return min(values, key=lambda value: ranked.get(value, len(SEVERITIES)))


def _osv_group_severities(payload: Mapping[str, Any]) -> dict[str, str]:
    severities: dict[str, str] = {}
    groups = payload.get("groups", [])
    if not isinstance(groups, list):
        return severities
    for group in groups:
        if not isinstance(group, dict):
            continue
        severity = _severity(group.get("max_severity"))
        identifiers = [
            value
            for field in ("ids", "aliases")
            for value in group.get(field, [])
            if isinstance(group.get(field), list) and isinstance(value, str) and value
        ]
        for identifier in identifiers:
            current = severities.get(identifier, "informational")
            severities[identifier] = _highest_severity(current, severity)
    return severities


def _canonical_vulnerability_id(identifiers: Iterable[str]) -> str:
    values = sorted({value for value in identifiers if value})
    for prefix in ("CVE-", "GHSA-", "PYSEC-", "OSV-"):
        matches = [value for value in values if value.upper().startswith(prefix)]
        if matches:
            return matches[0]
    return values[0] if values else "OSV-UNKNOWN"


def coalesce_osv_aliases(findings: Sequence[Finding]) -> list[Finding]:
    """Merge advisory records that describe the same aliased vulnerability."""
    groups: list[list[Finding]] = []
    for finding in findings:
        identifiers = {finding.rule_or_vulnerability_id, *finding.aliases}
        matching: list[int] = []
        for index, group in enumerate(groups):
            representative = group[0]
            if (
                representative.asset_identity != finding.asset_identity
                or representative.package != finding.package
                or representative.package_version != finding.package_version
                or representative.ecosystem != finding.ecosystem
            ):
                continue
            group_identifiers = {
                value
                for member in group
                for value in (member.rule_or_vulnerability_id, *member.aliases)
            }
            if identifiers & group_identifiers:
                matching.append(index)
        if not matching:
            groups.append([finding])
            continue
        merged = [finding]
        for index in reversed(matching):
            merged.extend(groups.pop(index))
        groups.append(merged)

    result: list[Finding] = []
    for group in groups:
        representative = group[0]
        identifiers = {
            value
            for finding in group
            for value in (finding.rule_or_vulnerability_id, *finding.aliases)
        }
        identifier = _canonical_vulnerability_id(identifiers)
        result.append(
            make_finding(
                scanner=representative.scanner,
                scanner_version=representative.scanner_version,
                database_identity=representative.scanner_database_identity,
                identifier=identifier,
                source=representative.source_commit,
                asset_type=representative.asset_type,
                asset_identity=representative.asset_identity,
                repository_path=representative.repository_path,
                aliases=identifiers - {identifier},
                package=representative.package,
                package_version=representative.package_version,
                ecosystem=representative.ecosystem,
                severity=_highest_severity(*(finding.severity for finding in group)),
                exploitability=representative.exploitability,
                fix_status=(
                    "fixed_available"
                    if any(finding.fix_status == "fixed_available" for finding in group)
                    else representative.fix_status
                ),
                scope=representative.scope,
                references={reference for finding in group for reference in finding.references},
            )
        )
    return result


def parse_osv(
    payload: Mapping[str, Any],
    *,
    version: str,
    database_identity: str,
    source: str,
    lock_path: str,
) -> list[Finding]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ScanFailure("invalid OSV JSON", exit_code=EXIT_SCANNER_FAILURE)
    packages_seen = 0
    findings: list[Finding] = []
    group_severities = _osv_group_severities(payload)
    for result in results:
        if not isinstance(result, dict):
            continue
        packages = result.get("packages", [])
        if not isinstance(packages, list):
            continue
        packages_seen += len(packages)
        for package_entry in packages:
            if not isinstance(package_entry, dict):
                continue
            package_data = (
                package_entry.get("package")
                if isinstance(package_entry.get("package"), dict)
                else {}
            )
            name = str(package_data.get("name") or package_entry.get("name") or "")
            installed_version = str(
                package_data.get("version") or package_entry.get("version") or ""
            )
            ecosystem = str(package_data.get("ecosystem") or "")
            vulnerabilities = package_entry.get("vulnerabilities", [])
            if not isinstance(vulnerabilities, list):
                continue
            for vulnerability in vulnerabilities:
                if not isinstance(vulnerability, dict):
                    continue
                identifier = str(vulnerability.get("id") or "OSV-UNKNOWN")
                aliases = vulnerability.get("aliases", [])
                normalized_ids = {
                    identifier,
                    *(
                        value
                        for value in aliases
                        if isinstance(aliases, list) and isinstance(value, str)
                    ),
                }
                severity = _osv_severity(vulnerability)
                for normalized_id in normalized_ids:
                    severity = _highest_severity(
                        severity,
                        group_severities.get(normalized_id, "informational"),
                    )
                affected = vulnerability.get("affected", [])
                fixed_versions: set[str] = set()
                if isinstance(affected, list):
                    for affected_item in affected:
                        if not isinstance(affected_item, dict):
                            continue
                        for range_item in affected_item.get("ranges", []):
                            if not isinstance(range_item, dict):
                                continue
                            for event in range_item.get("events", []):
                                if isinstance(event, dict) and event.get("fixed"):
                                    fixed_versions.add(str(event["fixed"]))
                references = [
                    item.get("url")
                    for item in vulnerability.get("references", [])
                    if isinstance(item, dict)
                ]
                development = lock_path.endswith("requirements-dev.lock")
                findings.append(
                    make_finding(
                        scanner="osv",
                        scanner_version=version,
                        database_identity=database_identity,
                        identifier=identifier,
                        source=source,
                        asset_type="dependency",
                        asset_identity=lock_path,
                        repository_path=lock_path,
                        aliases=aliases if isinstance(aliases, list) else (),
                        package=name,
                        package_version=installed_version,
                        ecosystem=ecosystem,
                        severity=severity,
                        exploitability="development_only" if development else "unknown",
                        fix_status="fixed_available" if fixed_versions else "no_fix",
                        scope=_scope(lock_path, development=development),
                        references=references,
                    )
                )
    if packages_seen == 0 and not findings:
        raise ScanFailure(f"OSV found no packages in {lock_path}", exit_code=EXIT_SCANNER_FAILURE)
    return coalesce_osv_aliases(findings)


def parse_gitleaks(
    payload: object,
    *,
    version: str,
    database_identity: str,
    source: str,
) -> list[Finding]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ScanFailure("invalid Gitleaks JSON", exit_code=EXIT_SCANNER_FAILURE)
    findings: list[Finding] = []
    for result in payload:
        if not isinstance(result, dict):
            continue
        path = normalize_repository_path(str(result.get("File") or result.get("file") or ""))
        identifier = str(result.get("RuleID") or result.get("rule_id") or "gitleaks-unknown")
        commit = str(result.get("Commit") or result.get("commit") or source)
        asset_identity = f"{path}@{commit}" if COMMIT_SHA.fullmatch(commit) else path
        findings.append(
            make_finding(
                scanner="gitleaks",
                scanner_version=version,
                database_identity=database_identity,
                identifier=identifier,
                source=source,
                asset_type="potential_secret",
                asset_identity=asset_identity,
                repository_path=path,
                severity="high",
                exploitability="reachable",
                fix_status="mitigation_available",
                scope=_scope(path),
            )
        )
    return findings


def parse_trivy(
    payload: Mapping[str, Any],
    *,
    version: str,
    database_identity: str,
    source: str,
    asset_identity: str,
    scope: str,
) -> list[Finding]:
    results = payload.get("Results", [])
    if results is None:
        return []
    if not isinstance(results, list):
        raise ScanFailure("invalid Trivy JSON", exit_code=EXIT_SCANNER_FAILURE)
    findings: list[Finding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = str(result.get("Target") or asset_identity)
        path = ""
        image_internal_path = (
            asset_identity.startswith("sha256:")
            and target.startswith("/")
            and not target.startswith("/work/")
        )
        if image_internal_path:
            try:
                path = normalize_repository_path(target.removeprefix("/"))
            except ScanFailure:
                path = ""
        elif not target.startswith("sha256:") and "/" in target:
            try:
                path = normalize_repository_path(target)
            except ScanFailure:
                path = ""
        for vulnerability in result.get("Vulnerabilities") or []:
            if not isinstance(vulnerability, dict):
                continue
            identifier = str(vulnerability.get("VulnerabilityID") or "TRIVY-UNKNOWN")
            fixed_version = str(vulnerability.get("FixedVersion") or "")
            references = vulnerability.get("References") or []
            vendor_ids = vulnerability.get("VendorIDs") or []
            if isinstance(vendor_ids, str):
                vendor_ids = [vendor_ids]
            findings.append(
                make_finding(
                    scanner="trivy",
                    scanner_version=version,
                    database_identity=database_identity,
                    identifier=identifier,
                    source=source,
                    asset_type="image_vulnerability",
                    asset_identity=asset_identity,
                    repository_path=path,
                    aliases=vendor_ids if isinstance(vendor_ids, list) else (),
                    package=str(vulnerability.get("PkgName") or ""),
                    package_version=str(vulnerability.get("InstalledVersion") or ""),
                    ecosystem=str(result.get("Type") or ""),
                    severity=_severity(vulnerability.get("Severity")),
                    exploitability="unknown",
                    fix_status="fixed_available" if fixed_version else "no_fix",
                    scope=scope,
                    references=references if isinstance(references, list) else (),
                )
            )
        for secret in result.get("Secrets") or []:
            if not isinstance(secret, dict):
                continue
            identifier = str(secret.get("RuleID") or "trivy-secret")
            findings.append(
                make_finding(
                    scanner="trivy",
                    scanner_version=version,
                    database_identity=database_identity,
                    identifier=identifier,
                    source=source,
                    asset_type="potential_secret",
                    asset_identity=asset_identity,
                    repository_path=path,
                    severity=_severity(secret.get("Severity"), default="high"),
                    exploitability="reachable",
                    fix_status="mitigation_available",
                    scope=scope,
                )
            )
        for misconfiguration in result.get("Misconfigurations") or []:
            if not isinstance(misconfiguration, dict):
                continue
            identifier = str(misconfiguration.get("ID") or "TRIVY-CONFIG-UNKNOWN")
            findings.append(
                make_finding(
                    scanner="trivy",
                    scanner_version=version,
                    database_identity=database_identity,
                    identifier=identifier,
                    source=source,
                    asset_type="configuration",
                    asset_identity=asset_identity,
                    repository_path=path,
                    severity=_severity(misconfiguration.get("Severity")),
                    exploitability="build_only",
                    fix_status="mitigation_available",
                    scope=scope,
                    references=(str(misconfiguration.get("PrimaryURL") or ""),),
                )
            )
    return findings


def _walk_dicts(value: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def parse_zizmor(
    payload: object,
    *,
    version: str,
    database_identity: str,
    source: str,
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, int]] = set()
    records = payload if isinstance(payload, list) else list(_walk_dicts(payload))
    for item in records:
        if not isinstance(item, dict) or item.get("ignored") is True:
            continue
        identifier = item.get("ident") or item.get("rule") or item.get("audit")
        if not identifier:
            continue
        determinations = (
            item.get("determinations") if isinstance(item.get("determinations"), dict) else {}
        )
        severity = _severity(
            determinations.get("severity") or item.get("severity"),
            default="medium",
        )
        locations = item.get("locations")
        if not isinstance(locations, list):
            legacy = item.get("location")
            locations = [legacy] if isinstance(legacy, dict) else [item]
        for location in locations:
            if not isinstance(location, dict):
                continue
            symbolic = (
                location.get("symbolic") if isinstance(location.get("symbolic"), dict) else {}
            )
            key_metadata = symbolic.get("key") if isinstance(symbolic.get("key"), dict) else {}
            local_metadata = (
                key_metadata.get("Local") if isinstance(key_metadata.get("Local"), dict) else {}
            )
            path_value = (
                local_metadata.get("verbatim_path")
                or location.get("path")
                or item.get("path")
                or item.get("filename")
                or item.get("file")
            )
            if not path_value:
                continue
            try:
                path = normalize_repository_path(str(path_value))
            except ScanFailure:
                continue
            concrete = (
                location.get("concrete") if isinstance(location.get("concrete"), dict) else {}
            )
            concrete_location = (
                concrete.get("location") if isinstance(concrete.get("location"), dict) else {}
            )
            start_point = (
                concrete_location.get("start_point")
                if isinstance(concrete_location.get("start_point"), dict)
                else {}
            )
            line = int(start_point.get("row") or location.get("row") or item.get("line") or 0)
            key = (str(identifier), path, line)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                make_finding(
                    scanner="zizmor",
                    scanner_version=version,
                    database_identity=database_identity,
                    identifier=str(identifier),
                    source=source,
                    asset_type="github_workflow",
                    asset_identity=f"{path}:{line}",
                    repository_path=path,
                    severity=severity,
                    exploitability="build_only",
                    fix_status="mitigation_available",
                    scope="github_workflow",
                )
            )
    return findings


def apply_dependency_scope_overrides(
    findings: Sequence[Finding],
    overrides: object,
) -> list[Finding]:
    if not isinstance(overrides, list):
        raise ScanFailure(
            "dependency scope overrides must be a list",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    indexed: dict[tuple[str, str, str], tuple[str, str]] = {}
    for item in overrides:
        if not isinstance(item, dict):
            raise ScanFailure(
                "invalid dependency scope override",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        key = (
            normalize_repository_path(str(item.get("lock_path") or "")),
            str(item.get("package") or ""),
            str(item.get("package_version") or ""),
        )
        if not all(key) or key in indexed:
            raise ScanFailure(
                "invalid or duplicate dependency scope override",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        scope = str(item.get("scope") or "")
        exploitability = str(item.get("exploitability") or "")
        if scope != "first_party_development" or exploitability != "development_only":
            raise ScanFailure(
                "dependency scope override may only narrow to development",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        evidence = str(item.get("evidence") or "")
        if not evidence or "*" in evidence:
            raise ScanFailure(
                "dependency scope override requires exact evidence",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        indexed[key] = (scope, exploitability)

    result: list[Finding] = []
    for finding in findings:
        override = indexed.get((finding.repository_path, finding.package, finding.package_version))
        if finding.scanner != "osv" or override is None:
            result.append(finding)
            continue
        result.append(
            replace(
                finding,
                scope=override[0],
                exploitability=override[1],
            )
        )
    return result


def apply_image_reachability_overrides(
    findings: Sequence[Finding],
    overrides: object,
) -> list[Finding]:
    """Apply exact, evidence-bearing reachability decisions to first-party images."""

    if not isinstance(overrides, list):
        raise ScanFailure(
            "image reachability overrides must be a list",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    indexed: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for item in overrides:
        if not isinstance(item, dict) or set(item) != {
            "asset_identities",
            "vulnerability_id",
            "packages",
            "package_version",
            "exploitability",
            "evidence",
        }:
            raise ScanFailure(
                "invalid image reachability override",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        assets = item["asset_identities"]
        packages = item["packages"]
        vulnerability_id = str(item["vulnerability_id"])
        package_version = str(item["package_version"])
        exploitability = str(item["exploitability"])
        evidence = str(item["evidence"])
        if (
            not isinstance(assets, list)
            or not assets
            or not all(isinstance(value, str) and SHA256.fullmatch(value) for value in assets)
            or len(set(assets)) != len(assets)
            or not isinstance(packages, list)
            or not packages
            or not all(isinstance(value, str) and value and "*" not in value for value in packages)
            or len(set(packages)) != len(packages)
            or not vulnerability_id
            or "*" in vulnerability_id
            or not package_version
            or "*" in package_version
            or exploitability not in {"reachable", "likely_reachable", "unknown", "not_present"}
            or not evidence.strip()
            or "*" in evidence
        ):
            raise ScanFailure(
                "unsafe image reachability override",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        for asset in assets:
            for package in packages:
                key = (asset, vulnerability_id, package, package_version)
                if key in indexed:
                    raise ScanFailure(
                        "duplicate image reachability override",
                        exit_code=EXIT_INVALID_GOVERNANCE,
                    )
                indexed[key] = (exploitability, evidence)

    matched: set[tuple[str, str, str, str]] = set()
    result: list[Finding] = []
    for finding in findings:
        if (
            finding.scanner != "trivy"
            or finding.scope != "first_party_runtime"
            or finding.severity not in {"critical", "high"}
        ):
            result.append(finding)
            continue
        key = (
            finding.asset_identity,
            finding.rule_or_vulnerability_id,
            finding.package,
            finding.package_version,
        )
        override = indexed.get(key)
        if override is None:
            raise ScanFailure(
                "first-party image finding lacks exact reachability evidence",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        matched.add(key)
        result.append(replace(finding, exploitability=override[0]))
    if matched != set(indexed):
        raise ScanFailure(
            "image reachability evidence does not match exact findings",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    return result


def classify_findings(
    findings: Sequence[Finding],
    *,
    baseline: Mapping[str, Mapping[str, Any]],
    exceptions: Mapping[str, Mapping[str, Any]],
    expired: set[str],
    advisory_scanners: frozenset[str] = frozenset(),
) -> list[Finding]:
    classified: list[Finding] = []
    for finding in findings:
        fingerprint = finding.safe_fingerprint
        state = "new"
        first_seen = finding.first_seen_commit
        if fingerprint in expired:
            state = "expired_exception"
        elif fingerprint in baseline:
            state = "baseline"
            first_seen = str(baseline[fingerprint]["first_seen_commit"])
        elif finding.scanner in advisory_scanners:
            state = "advisory_new"
        if state not in REGRESSION_STATES:
            raise ScanFailure("invalid regression state", exit_code=EXIT_INVALID_GOVERNANCE)
        classified.append(replace(finding, regression_state=state, first_seen_commit=first_seen))
    return classified


def _is_non_suppressible(finding: Finding) -> bool:
    if finding.regression_state == "expired_exception":
        return True
    if finding.asset_type == "confirmed_secret":
        return True
    return finding.exploitability == "known_exploited" and finding.scope == "first_party_runtime"


def is_blocking(
    finding: Finding,
    *,
    exceptions: Mapping[str, Mapping[str, Any]],
) -> bool:
    if _is_non_suppressible(finding):
        return True
    exception = exceptions.get(finding.safe_fingerprint)
    if exception is not None:
        expected = {
            "scanner": finding.scanner,
            "finding_identifier": finding.rule_or_vulnerability_id,
            "asset": finding.asset_identity,
            "scope": finding.scope,
            "severity": finding.severity,
            "exploitability": finding.exploitability,
        }
        if any(exception.get(key) != value for key, value in expected.items()):
            raise ScanFailure(
                "SCN exception metadata mismatch",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
        exception_fix_status = exception.get("fix_status")
        if (
            exception_fix_status not in {"false_positive", "disputed", "withdrawn"}
            and exception_fix_status != finding.fix_status
        ):
            raise ScanFailure(
                "SCN exception fix-status mismatch",
                exit_code=EXIT_INVALID_GOVERNANCE,
            )
    has_exception = exception is not None
    if finding.scope == "first_party_runtime" and finding.severity in {"critical", "high"}:
        return not has_exception
    if finding.scope == "github_workflow" and finding.severity in {"critical", "high"}:
        return not has_exception
    if finding.scope == "vendor_runtime" and finding.regression_state in {
        "new",
        "resurfaced",
        "expired_exception",
    }:
        return finding.severity in {"critical", "high"} and not has_exception
    if finding.asset_type == "potential_secret" and finding.regression_state in {
        "new",
        "resurfaced",
    }:
        return not has_exception
    return False


def _docker_base(
    *,
    network: bool,
    mounts: Sequence[tuple[Path, str, bool]] = (),
    additional_tmpfs: Sequence[str] = (),
    temporary_storage_size: str = "256m",
) -> list[str]:
    if re.fullmatch(r"[1-9][0-9]*(?:m|g)", temporary_storage_size) is None:
        raise ScanFailure(
            "invalid scanner temporary-storage limit",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,noexec,size={temporary_storage_size},mode=1777",
    ]
    if not network:
        command.extend(["--network", "none"])
    for tmpfs in additional_tmpfs:
        command.extend(["--tmpfs", tmpfs])
    for source, destination, readonly in mounts:
        spec = f"type=bind,source={source.resolve()},target={destination}"
        if readonly:
            spec += ",readonly"
        command.extend(["--mount", spec])
    return command


def _run(
    command: Sequence[str],
    *,
    accepted: frozenset[int] = frozenset({0}),
    safe_name: str,
    timeout_seconds: float = 900,
) -> tuple[str, str, int]:
    if timeout_seconds <= 0 or timeout_seconds > 1800:
        raise ScanFailure(
            "invalid scanner timeout",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    try:
        result = subprocess.run(
            list(command),
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise ScanFailure(
            f"{safe_name} execution exceeded its bounded timeout",
            exit_code=EXIT_SCANNER_FAILURE,
        ) from error
    if result.returncode not in accepted:
        raise ScanFailure(
            f"{safe_name} execution failed with status {result.returncode}",
            exit_code=EXIT_SCANNER_FAILURE,
        )
    return result.stdout, result.stderr, result.returncode


def _parse_json_output(stdout: str, *, scanner: str, allow_empty: bool = True) -> object:
    stripped = stdout.strip()
    if not stripped and allow_empty:
        return []
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ScanFailure(
            f"{scanner} returned invalid JSON", exit_code=EXIT_SCANNER_FAILURE
        ) from error


class DockerScannerRunner:
    def __init__(
        self,
        *,
        snapshot: Path,
        output: Path,
        scanners: Mapping[str, Mapping[str, Any]],
        source: str,
    ) -> None:
        self.snapshot = snapshot
        self.output = output
        self.scanners = scanners
        self.source = source
        self.executions: list[ScannerExecution] = []

    def validate_version(self, name: str) -> None:
        scanner = self.scanners[name]
        arguments = {
            "semgrep": ["semgrep", "--version"],
            "osv": ["--version"],
            "gitleaks": ["version"],
            "trivy": ["--version"],
            "zizmor": ["--version"],
        }[name]
        command = [
            *_docker_base(
                network=False,
                additional_tmpfs=(
                    ("/root/.semgrep:rw,nosuid,nodev,noexec,size=16m,mode=700"),
                    "/root/.cache:rw,nosuid,nodev,noexec,size=64m,mode=700",
                )
                if name == "semgrep"
                else (),
            ),
            *(["--env", "HOME=/tmp"] if name == "semgrep" else []),
            str(scanner["image"]),
            *arguments,
        ]
        stdout, stderr, _ = _run(command, safe_name=f"{name} version")
        rendered = f"{stdout}\n{stderr}"
        if re.search(str(scanner["expected_version_pattern"]), rendered) is None:
            raise ScanFailure(f"{name} version identity mismatch", exit_code=EXIT_TOOL_IDENTITY)

    def semgrep(self) -> list[Finding]:
        name = "semgrep"
        self.validate_version(name)
        scanner = self.scanners[name]
        command = [
            *_docker_base(
                network=False,
                mounts=((self.snapshot, "/src", True),),
                additional_tmpfs=(
                    "/root/.semgrep:rw,nosuid,nodev,noexec,size=16m,mode=700",
                    "/root/.cache:rw,nosuid,nodev,noexec,size=64m,mode=700",
                ),
            ),
            "--env",
            "HOME=/tmp",
            "--workdir",
            "/src",
            str(scanner["image"]),
            "semgrep",
            "scan",
            "--config",
            "/src/security/scanning/semgrep.yml",
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--no-autofix",
            "--error",
            "apps/portal-api/app",
            "apps/portal-web",
            "scripts",
        ]
        stdout, _, _ = _run(command, accepted=frozenset({0, 1}), safe_name="Semgrep")
        database_identity = str(scanner["ruleset_sha256"])
        payload = _parse_json_output(stdout, scanner=name)
        if not isinstance(payload, dict):
            raise ScanFailure("invalid Semgrep result", exit_code=EXIT_SCANNER_FAILURE)
        findings = parse_semgrep(
            payload,
            version=str(scanner["version"]),
            database_identity=database_identity,
            source=self.source,
        )
        self._record(name, database_identity, sha256_file(SCANNING_ROOT / "semgrep.yml"), findings)
        return findings

    def osv(self) -> list[Finding]:
        name = "osv"
        self.validate_version(name)
        scanner = self.scanners[name]
        findings: list[Finding] = []
        inputs = (
            "apps/portal-api/requirements.lock",
            "apps/portal-api/requirements-dev.lock",
            "pnpm-lock.yaml",
        )
        input_digests: dict[str, str] = {}
        for lock_path in inputs:
            local = self.snapshot / lock_path
            if not local.is_file():
                raise ScanFailure(
                    f"missing OSV lock input: {lock_path}", exit_code=EXIT_INPUT_MISMATCH
                )
            input_digests[lock_path] = sha256_file(local)
            scan_input = local
            mounts: tuple[tuple[Path, str, bool], ...] = ((self.snapshot, "/src", True),)
            scanner_path = f"/src/{lock_path}"
            if lock_path.endswith(".lock"):
                osv_inputs = self.output / "work" / "osv"
                osv_inputs.mkdir(parents=True, exist_ok=True)
                compatibility_name = (
                    "requirements-dev.txt"
                    if lock_path.endswith("requirements-dev.lock")
                    else "requirements.txt"
                )
                scan_input = osv_inputs / compatibility_name
                shutil.copyfile(local, scan_input)
                if sha256_file(scan_input) != input_digests[lock_path]:
                    raise ScanFailure(
                        f"OSV compatibility input mismatch: {lock_path}",
                        exit_code=EXIT_INPUT_MISMATCH,
                    )
                mounts = ((self.snapshot, "/src", True), (osv_inputs, "/inputs", True))
                scanner_path = f"/inputs/{compatibility_name}"
            command = [
                *_docker_base(network=True, mounts=mounts),
                str(scanner["image"]),
                "scan",
                "source",
                "--format=json",
                "--all-packages",
                f"--lockfile={scanner_path}",
            ]
            stdout, _, _ = _run(command, accepted=frozenset({0, 1}), safe_name=f"OSV {lock_path}")
            payload = _parse_json_output(stdout, scanner=name)
            if not isinstance(payload, dict):
                raise ScanFailure("invalid OSV result", exit_code=EXIT_SCANNER_FAILURE)
            findings.extend(
                parse_osv(
                    payload,
                    version=str(scanner["version"]),
                    database_identity=str(scanner["database_identity"]),
                    source=self.source,
                    lock_path=lock_path,
                )
            )
        self._record(
            name,
            str(scanner["database_identity"]),
            canonical_digest(input_digests),
            findings,
        )
        return findings

    def gitleaks_tracked(self) -> list[Finding]:
        name = "gitleaks"
        self.validate_version(name)
        scanner = self.scanners[name]
        command = [
            *_docker_base(network=False, mounts=((self.snapshot, "/src", True),)),
            str(scanner["image"]),
            "dir",
            "/src",
            "--config",
            "/src/security/scanning/gitleaks.toml",
            "--no-banner",
            "--redact=100",
            "--report-format=json",
            "--report-path=/dev/stdout",
            "--exit-code=0",
        ]
        stdout, _, _ = _run(command, safe_name="Gitleaks tracked")
        payload = _parse_json_output(stdout, scanner=name)
        findings = parse_gitleaks(
            payload,
            version=str(scanner["version"]),
            database_identity=str(scanner["ruleset_sha256"]),
            source=self.source,
        )
        self._record(
            name,
            str(scanner["ruleset_sha256"]),
            canonical_digest(git("ls-files", "--stage").splitlines()),
            findings,
        )
        return findings

    def gitleaks_history(self, *, log_options: str = "--all") -> list[Finding]:
        name = "gitleaks"
        self.validate_version(name)
        scanner = self.scanners[name]
        history = create_history_snapshot(self.output)
        command = [
            *_docker_base(
                network=False,
                mounts=((history, "/repo", True), (self.snapshot, "/src", True)),
            ),
            str(scanner["image"]),
            "git",
            "/repo",
            "--config",
            "/src/security/scanning/gitleaks.toml",
            f"--log-opts={log_options}",
            "--no-banner",
            "--redact=100",
            "--report-format=json",
            "--report-path=/dev/stdout",
            "--exit-code=0",
        ]
        stdout, _, _ = _run(command, safe_name="Gitleaks history")
        payload = _parse_json_output(stdout, scanner=name)
        findings = parse_gitleaks(
            payload,
            version=str(scanner["version"]),
            database_identity=str(scanner["ruleset_sha256"]),
            source=self.source,
        )
        self._record(
            name,
            str(scanner["ruleset_sha256"]),
            canonical_digest({"log_options": log_options, "head": self.source}),
            findings,
        )
        return findings

    def zizmor(self) -> list[Finding]:
        name = "zizmor"
        self.validate_version(name)
        scanner = self.scanners[name]
        workflows = sorted((self.snapshot / ".github" / "workflows").glob("*.y*ml"))
        if not workflows:
            raise ScanFailure("no workflow inputs found", exit_code=EXIT_INPUT_MISMATCH)
        targets = [f"/src/{path.relative_to(self.snapshot).as_posix()}" for path in workflows]
        command = [
            *_docker_base(network=False, mounts=((self.snapshot, "/src", True),)),
            str(scanner["image"]),
            "--offline",
            "--persona=auditor",
            "--format=json-v1",
            "--collect=workflows",
            *targets,
        ]
        stdout, _, _ = _run(
            command,
            accepted=frozenset({0, 11, 12, 13, 14}),
            safe_name="zizmor",
        )
        payload = _parse_json_output(stdout, scanner=name)
        findings = parse_zizmor(
            payload,
            version=str(scanner["version"]),
            database_identity=str(scanner["ruleset_identity"]),
            source=self.source,
        )
        self._record(
            name,
            str(scanner["ruleset_identity"]),
            canonical_digest({path.name: sha256_file(path) for path in workflows}),
            findings,
        )
        return findings

    def trivy_config(self) -> list[Finding]:
        name = "trivy"
        self.validate_version(name)
        scanner = self.scanners[name]
        findings: list[Finding] = []
        inputs = (
            "apps/portal-api/Dockerfile",
            "apps/portal-web/Dockerfile",
            "docker-compose.yml",
        )
        for path in inputs:
            command = [
                *_docker_base(network=True, mounts=((self.snapshot, "/src", True),)),
                str(scanner["image"]),
                "config",
                "--format=json",
                "--quiet",
                f"/src/{path}",
            ]
            stdout, _, _ = _run(command, safe_name=f"Trivy config {path}")
            payload = _parse_json_output(stdout, scanner=name)
            if not isinstance(payload, dict):
                raise ScanFailure("invalid Trivy config result", exit_code=EXIT_SCANNER_FAILURE)
            findings.extend(
                parse_trivy(
                    payload,
                    version=str(scanner["version"]),
                    database_identity="trivy-checks-0.70.0",
                    source=self.source,
                    asset_identity=path,
                    scope="first_party_development",
                )
            )
        self._record(
            name,
            "trivy-checks-0.70.0",
            canonical_digest({path: sha256_file(self.snapshot / path) for path in inputs}),
            findings,
        )
        return findings

    def trivy_image(self, tag: str, *, scope: str) -> list[Finding]:
        name = "trivy"
        self.validate_version(name)
        scanner = self.scanners[name]
        image_id_before = self._image_id(tag)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip("-")[:80] or "image"
        image_dir = self.output / "work" / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        archive = image_dir / f"{safe_name}.tar"
        _run(
            ["docker", "image", "save", "--output", str(archive), tag],
            safe_name=f"Docker image export {safe_name}",
        )
        image_id_after = self._image_id(tag)
        if image_id_after != image_id_before:
            raise ScanFailure("image identity changed during export", exit_code=EXIT_INPUT_MISMATCH)
        cache = self.output / "cache" / "trivy"
        cache.mkdir(parents=True, exist_ok=True)
        command = [
            *_docker_base(
                network=True,
                mounts=((image_dir, "/work", True), (cache, "/cache", False)),
                temporary_storage_size="4g",
            ),
            str(scanner["image"]),
            "image",
            "--cache-dir=/cache",
            "--format=json",
            "--quiet",
            "--scanners=vuln,secret",
            f"--input=/work/{archive.name}",
        ]
        stdout, _, _ = _run(command, safe_name=f"Trivy image {safe_name}")
        payload = _parse_json_output(stdout, scanner=name)
        if not isinstance(payload, dict):
            raise ScanFailure("invalid Trivy image result", exit_code=EXIT_SCANNER_FAILURE)
        database_identity = self._trivy_database_identity(cache)
        findings = parse_trivy(
            payload,
            version=str(scanner["version"]),
            database_identity=database_identity,
            source=self.source,
            asset_identity=image_id_before,
            scope=scope,
        )
        self._record(name, database_identity, image_id_before, findings)
        return findings

    @staticmethod
    def _image_id(tag: str) -> str:
        stdout, _, _ = _run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            safe_name="Docker image identity",
        )
        value = stdout.strip()
        if not SHA256.fullmatch(value):
            raise ScanFailure("invalid Docker image identity", exit_code=EXIT_INPUT_MISMATCH)
        return value

    def _trivy_database_identity(self, cache: Path) -> str:
        scanner = self.scanners["trivy"]
        command = [
            *_docker_base(network=False, mounts=((cache, "/cache", False),)),
            str(scanner["image"]),
            "--cache-dir=/cache",
            "version",
            "--format=json",
        ]
        stdout, _, _ = _run(command, safe_name="Trivy database identity")
        payload = _parse_json_output(stdout, scanner="trivy")
        identity = canonical_digest(payload)
        timestamps = [
            value
            for item in _walk_dicts(payload)
            for key, value in item.items()
            if str(key).lower() in {"updatedat", "downloadedat"}
        ]
        parsed: list[datetime] = []
        for value in timestamps:
            try:
                parsed.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
            except ValueError:
                continue
        if parsed:
            newest = max(parsed)
            age_hours = (datetime.now(UTC) - newest.astimezone(UTC)).total_seconds() / 3600
            if age_hours > int(scanner["database_max_age_hours"]):
                raise ScanFailure("Trivy database is stale", exit_code=EXIT_STALE_DATABASE)
        return identity

    def _record(
        self,
        name: str,
        database_identity: str,
        input_identity: str,
        findings: Sequence[Finding],
    ) -> None:
        scanner = self.scanners[name]
        self.executions.append(
            ScannerExecution(
                scanner=name,
                version=str(scanner["version"]),
                immutable_image=str(scanner["image"]),
                database_identity=database_identity,
                input_identity=input_identity,
                finding_count=len(findings),
            )
        )


def _counts(findings: Sequence[Finding]) -> dict[str, dict[str, int]]:
    return {
        "severity": dict(sorted(Counter(item.severity for item in findings).items())),
        "regression_state": dict(
            sorted(Counter(item.regression_state for item in findings).items())
        ),
        "scope": dict(sorted(Counter(item.scope for item in findings).items())),
        "scanner": dict(sorted(Counter(item.scanner for item in findings).items())),
    }


def build_report(
    *,
    mode: str,
    source: str,
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    exceptions: Mapping[str, Any],
    findings: Sequence[Finding],
    executions: Sequence[ScannerExecution],
    blocking: Sequence[Finding],
    started_at: datetime,
    artifact_manifest_identity: str = "",
) -> dict[str, Any]:
    result = "blocked" if blocking else "passed"
    report = {
        "schema_version": "portal-security-report/v1",
        "mode": mode,
        "source_commit": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "policy_version": policy["policy_version"],
        "policy_digest": canonical_digest(policy),
        "baseline_digest": canonical_digest(baseline),
        "exception_digest": canonical_digest(exceptions),
        "artifact_manifest_identity": artifact_manifest_identity,
        "result": result,
        "execution_status": "completed",
        "exit_code": EXIT_BLOCKING_FINDING if blocking else EXIT_PASSED,
        "counts": _counts(findings),
        "blocking_fingerprints": sorted(item.safe_fingerprint for item in blocking),
        "scanner_executions": [asdict(item) for item in executions],
        "findings": [
            {
                **asdict(item),
                "aliases": list(item.aliases),
                "references": list(item.references),
            }
            for item in sorted(
                findings,
                key=lambda value: (
                    value.scanner,
                    value.rule_or_vulnerability_id,
                    value.asset_identity,
                    value.safe_fingerprint,
                ),
            )
        ],
        "coverage_limitations": [
            "Root data-platform Python dependencies lack an immutable lock and "
            "are not claimed complete.",
            "Semgrep CE does not provide complete cross-file or framework-aware analysis.",
            "Advisory databases and vendor remediation may lag upstream disclosures.",
        ],
    }
    assert_safe_report(report)
    return report


def _write_report(output: Path, mode: str, report: Mapping[str, Any]) -> Path:
    safe_output = _safe_output_root(output)
    safe_output.mkdir(parents=True, exist_ok=True)
    path = safe_output / f"{mode}.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return path


def _policy_inputs() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    set[str],
]:
    toolchain = _json(TOOLCHAIN_PATH)
    policy = _json(POLICY_PATH)
    baseline = _json(BASELINE_PATH)
    exceptions = _json(EXCEPTIONS_PATH)
    if policy.get("schema_version") != "portal-security-policy/v1":
        raise ScanFailure("unsupported policy schema", exit_code=EXIT_INVALID_GOVERNANCE)
    policy_version = policy.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise ScanFailure("missing policy version", exit_code=EXIT_INVALID_GOVERNANCE)
    scanners = validate_toolchain(toolchain)
    baseline_index = validate_baseline(baseline, policy_version=policy_version)
    maximum_days = policy.get("exception_maximum_days")
    if not isinstance(maximum_days, dict):
        raise ScanFailure(
            "missing exception duration policy",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    _exception_index, expired = validate_exceptions(
        exceptions,
        maximum_days=maximum_days,
    )
    return (
        toolchain,
        policy,
        baseline,
        exceptions,
        scanners,
        baseline_index,
        expired,
    )


def run_mode(
    mode: str,
    *,
    output: Path = DEFAULT_OUTPUT,
    api_image: str = "fintech-portal-api:local",
    web_image: str = "fintech-payments-data-platform-portal-web",
) -> int:
    started_at = datetime.now(UTC)
    (
        _toolchain,
        policy,
        baseline,
        exceptions,
        scanners,
        baseline_index,
        expired,
    ) = _policy_inputs()
    maximum_days = policy.get("exception_maximum_days")
    if not isinstance(maximum_days, dict):
        raise ScanFailure(
            "missing exception duration policy",
            exit_code=EXIT_INVALID_GOVERNANCE,
        )
    exception_index, _ = validate_exceptions(
        exceptions,
        maximum_days=maximum_days,
    )
    if mode == "policy":
        print("security policy and immutable toolchain: valid")
        return EXIT_PASSED

    source = source_commit()
    snapshot = create_index_snapshot(output)
    runner = DockerScannerRunner(
        snapshot=snapshot,
        output=_safe_output_root(output),
        scanners=scanners,
        source=source,
    )
    findings: list[Finding] = []
    advisory_scanners: frozenset[str] = frozenset()
    artifact_manifest_identity = ""
    if mode in {"fast", "full"}:
        findings.extend(runner.semgrep())
        findings.extend(runner.osv())
        commit_range = pull_request_git_range() if mode == "fast" else None
        findings.extend(
            runner.gitleaks_history(log_options=commit_range)
            if commit_range is not None
            else runner.gitleaks_tracked()
        )
        findings.extend(runner.zizmor())
        if mode == "full":
            findings.extend(runner.trivy_config())
            advisory_scanners = frozenset({"osv", "trivy"})
    elif mode == "history":
        findings.extend(runner.gitleaks_history())
    elif mode == "images":
        artifact_manifest_identity = validate_artifact_manifest(
            output / ".." / "portal-artifacts" / "manifest.json",
            source=source,
            expected_image_ids={
                "portal-api": runner._image_id(api_image),
                "portal-web": runner._image_id(web_image),
            },
        )
        findings.extend(runner.trivy_config())
        findings.extend(runner.trivy_image(api_image, scope="first_party_runtime"))
        findings.extend(runner.trivy_image(web_image, scope="first_party_runtime"))
        vendor_images = _portal_vendor_images(snapshot / "docker-compose.yml")
        for image in vendor_images:
            findings.extend(runner.trivy_image(image, scope="vendor_runtime"))
        advisory_scanners = frozenset({"trivy"})
    else:
        raise ScanFailure("unsupported scan mode", exit_code=EXIT_INPUT_MISMATCH)

    findings = apply_dependency_scope_overrides(
        findings,
        policy.get("dependency_scope_overrides", []),
    )
    if mode == "images":
        findings = apply_image_reachability_overrides(
            findings,
            policy.get("image_reachability_overrides", []),
        )
    unique = {finding.safe_fingerprint: finding for finding in findings}
    classified = classify_findings(
        list(unique.values()),
        baseline=baseline_index,
        exceptions=exception_index,
        expired=expired,
        advisory_scanners=advisory_scanners,
    )
    blocking = [
        finding for finding in classified if is_blocking(finding, exceptions=exception_index)
    ]
    report = build_report(
        mode=mode,
        source=source,
        policy=policy,
        baseline=baseline,
        exceptions=exceptions,
        findings=classified,
        executions=runner.executions,
        blocking=blocking,
        started_at=started_at,
        artifact_manifest_identity=artifact_manifest_identity,
    )
    path = _write_report(output, mode, report)
    print(
        json.dumps(
            {
                "report": path.relative_to(REPOSITORY_ROOT).as_posix(),
                "result": report["result"],
                "findings": len(classified),
                "blocking": len(blocking),
            },
            sort_keys=True,
        )
    )
    return EXIT_BLOCKING_FINDING if blocking else EXIT_PASSED


def _portal_vendor_images(compose: Path) -> tuple[str, ...]:
    content = compose.read_text(encoding="utf-8")
    images: list[str] = []
    for service in ("portal-postgres", "portal-redis", "portal-keycloak"):
        match = re.search(
            rf"^  {service}:\s*$.*?^\s{{4}}image:\s*(\S+)\s*$",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if match is None or IMAGE_REFERENCE.fullmatch(match.group(1)) is None:
            raise ScanFailure(
                f"vendor image identity mismatch: {service}",
                exit_code=EXIT_INPUT_MISMATCH,
            )
        images.append(match.group(1))
    return tuple(images)


def validate_artifact_manifest(
    path: Path,
    *,
    source: str,
    expected_image_ids: Mapping[str, str],
) -> str:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ScanFailure("Portal artifact manifest is missing", exit_code=EXIT_INPUT_MISMATCH)
    manifest = _json(resolved)
    if manifest.get("schema_version") != "portal-artifact-build/v1":
        raise ScanFailure("Portal artifact manifest schema mismatch", exit_code=EXIT_INPUT_MISMATCH)
    repository = manifest.get("repository")
    artifact_source = repository.get("source_commit") if isinstance(repository, dict) else None
    if not isinstance(artifact_source, str) or not COMMIT_SHA.fullmatch(artifact_source):
        raise ScanFailure("Portal artifact source identity mismatch", exit_code=EXIT_INPUT_MISMATCH)
    if artifact_source != source:
        parent = git("rev-parse", f"{source}^")
        changed = frozenset(
            path
            for path in git("diff", "--name-only", f"{artifact_source}..{source}").splitlines()
            if path
        )
        if (
            parent != artifact_source
            or not changed
            or not changed <= ARTIFACT_GOVERNANCE_ONLY_PATHS
        ):
            raise ScanFailure(
                "Portal artifact source identity mismatch",
                exit_code=EXIT_INPUT_MISMATCH,
            )
    images = manifest.get("images")
    if not isinstance(images, list):
        raise ScanFailure(
            "Portal artifact image inventory is invalid", exit_code=EXIT_INPUT_MISMATCH
        )
    actual: dict[str, str] = {}
    for item in images:
        if not isinstance(item, dict):
            continue
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        name = str(item.get("name") or "")
        digest = str(output.get("digest") or "")
        if name and SHA256.fullmatch(digest):
            actual[name] = digest
    if actual != dict(expected_image_ids):
        raise ScanFailure("Portal artifact image identity mismatch", exit_code=EXIT_INPUT_MISMATCH)
    return sha256_file(resolved)


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("fast", "full", "images", "history", "policy"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-image", default="fintech-portal-api:local")
    parser.add_argument("--web-image", default="fintech-payments-data-platform-portal-web")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    try:
        return run_mode(
            options.mode,
            output=options.output,
            api_image=options.api_image,
            web_image=options.web_image,
        )
    except ScanFailure as error:
        print(
            json.dumps(
                {
                    "error": redact_text(str(error)),
                    "exit_code": error.exit_code,
                    "result": "execution_failed",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
