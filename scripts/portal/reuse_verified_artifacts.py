"""Materialize a Portal build manifest from exact FF-06A artifacts without rebuilding."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_reproducible_artifacts as artifacts  # noqa: E402

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_HANDOFF: Final = REPOSITORY_ROOT / "security" / "scanning" / "final-artifacts.json"
SHA256: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
GOVERNANCE_ONLY_PATHS: Final = frozenset(
    {
        "apps/portal-api/tests/unit/test_reproducible_artifact_tools.py",
        "docs/governance/reviews/PORTAL-002-workstream-b-completion-report-f002.md",
        "docs/portal/security-scanning.md",
        "scripts/portal/reuse_verified_artifacts.py",
        "scripts/security/scan.py",
        "security/scanning/baseline.json",
        "security/scanning/exceptions.json",
        "security/scanning/final-artifacts.json",
        "security/scanning/policy.json",
        "security/scanning/schemas/exceptions.schema.json",
        "security/scanning/schemas/finding.schema.json",
        "tests/security/test_security_scanning.py",
    }
)
CONTENT_ROOTS: Final = {
    "portal-api": ("/app", "/usr/local/lib/python3.11/site-packages"),
    "portal-web": ("/app",),
}


def run(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ValueError(f"verification command failed: {arguments[0]}")
    return result.stdout.strip()


def git(*arguments: str) -> str:
    return run(["git", *arguments])


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def image_inspect(tag: str) -> dict[str, Any]:
    value = json.loads(run(["docker", "image", "inspect", tag]))
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("invalid Docker image inspection")
    return value[0]


def package_inventory_digest(tag: str) -> str:
    output = run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "dpkg-query",
            tag,
            "-W",
            r"-f=${binary:Package}|${Version}|${Architecture}\n",
        ]
    )
    return canonical_digest(sorted(line for line in output.splitlines() if line))


def application_content_digest(tag: str, name: str) -> str:
    hashes = artifacts.application_hashes(tag, CONTENT_ROOTS[name])
    return canonical_digest(hashes)


def source_is_current_or_governance_child(*, artifact_source: str, current: str) -> bool:
    if artifact_source == current:
        return True
    parent = git("rev-parse", f"{current}^")
    changed = frozenset(
        line
        for line in git("diff", "--name-only", f"{artifact_source}..{current}").splitlines()
        if line
    )
    return parent == artifact_source and bool(changed) and changed <= GOVERNANCE_ONLY_PATHS


def load_handoff(path: Path) -> dict[str, Any]:
    try:
        handoff = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("invalid final-artifact handoff") from error
    if (
        not isinstance(handoff, dict)
        or handoff.get("schema_version") != "portal-final-artifacts/v1"
        or handoff.get("canonical_policy_identity") != "docker_image_id"
    ):
        raise ValueError("unsupported final-artifact handoff")
    return handoff


def verify_handoff(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    current = git("rev-parse", "HEAD")
    source = str(handoff.get("source_commit") or "")
    if not COMMIT_SHA.fullmatch(source) or not source_is_current_or_governance_child(
        artifact_source=source, current=current
    ):
        raise ValueError("final-artifact source identity mismatch")
    records = handoff.get("artifacts")
    if not isinstance(records, list) or len(records) != 2:
        raise ValueError("final-artifact inventory mismatch")
    verified: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid final-artifact record")
        name = str(record.get("name") or "")
        tag = str(record.get("local_tag") or "")
        expected_id = str(record.get("image_id") or "")
        if name not in CONTENT_ROOTS or not tag.startswith(f"{name}:ff06a-"):
            raise ValueError("unsafe final-artifact tag")
        inspected = image_inspect(tag)
        actual_id = str(inspected.get("Id") or "")
        if not SHA256.fullmatch(actual_id) or actual_id != expected_id:
            raise ValueError("final-artifact image identity mismatch")
        if str(inspected.get("Architecture") or "") != record.get("architecture"):
            raise ValueError("final-artifact architecture mismatch")
        if str(inspected.get("Os") or "") != record.get("os"):
            raise ValueError("final-artifact operating-system mismatch")
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if labels.get("org.opencontainers.image.revision") != source:
            raise ValueError("final-artifact revision label mismatch")
        if application_content_digest(tag, name) != record.get("application_content_digest"):
            raise ValueError("final-artifact application content mismatch")
        if package_inventory_digest(tag) != record.get("package_inventory_digest"):
            raise ValueError("final-artifact package inventory mismatch")
        verified.append(record)
    if {str(item["name"]) for item in verified} != set(CONTENT_ROOTS):
        raise ValueError("final-artifact names are incomplete")
    return sorted(verified, key=lambda item: str(item["name"]))


def build_manifest(handoff: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    source = str(handoff["source_commit"])
    source_epoch = int(git("show", "-s", "--format=%ct", source))
    return {
        "schema_version": "portal-artifact-build/v1",
        "repository": {
            "name": REPOSITORY_ROOT.name,
            "branch": "feat/portal-002-runtime-conformance",
            "source_commit": source,
        },
        "workflow": {
            "path": ".github/workflows/ci.yml",
            "sha256": artifacts.sha256_file(REPOSITORY_ROOT / ".github/workflows/ci.yml"),
            "actions": artifacts.pinned_portal_actions(
                REPOSITORY_ROOT / ".github/workflows/ci.yml"
            ),
        },
        "vendor_images": artifacts.pinned_vendor_images(REPOSITORY_ROOT / "docker-compose.yml"),
        "dependency_locks": handoff["dependency_locks"],
        "images": [
            {
                "name": record["name"],
                "platform": f"{record['os']}/{record['architecture']}",
                "context": ".",
                "dockerfile": record["dockerfile"],
                "base_images": record["base_images"],
                "output": {
                    "reference": record["local_tag"],
                    "digest": record["image_id"],
                },
                "clean_builds": {
                    "selection_mode": "single_final_build",
                    "comparison_performed": False,
                },
                "inspection": record["inspection"],
            }
            for record in records
        ],
        "build_tools": handoff["build_tools"],
        "reproducibility": {
            "repository_inputs": "immutable",
            "oci_image_identity": "not_verified",
            "source_date_epoch": source_epoch,
            "selection_mode": "reuse_exact_verified_artifacts",
            "blocking_nondeterminism": handoff["oci_nondeterminism"],
        },
        "metadata": {
            "generated_at_utc": records[0]["created"],
            "timestamp_affects_image_identity": False,
            "evidence_version": handoff["evidence_version"],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-clean", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.require_clean and (
            git("diff", "--name-only") or git("diff", "--cached", "--name-only")
        ):
            raise ValueError("a clean tracked Git worktree and index are required")
        handoff = load_handoff(args.handoff.resolve())
        records = verify_handoff(handoff)
        manifest = build_manifest(handoff, records)
        artifacts.manifest_has_sensitive_data(manifest)
        encoded = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        identity = f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"
        if identity != handoff.get("artifact_manifest_identity"):
            raise ValueError("materialized artifact manifest identity mismatch")
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "manifest": output.relative_to(REPOSITORY_ROOT).as_posix(),
                    "identity": identity,
                    "images": {str(record["name"]): str(record["image_id"]) for record in records},
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Verified artifact reuse failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
