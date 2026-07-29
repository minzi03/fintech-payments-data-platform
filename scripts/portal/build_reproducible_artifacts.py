"""Build Portal images twice and emit a non-secret reproducibility manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
PLATFORM: Final = "linux/amd64"
PINNED_REFERENCE: Final = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
ACTION_REFERENCE: Final = re.compile(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
FRONTEND_REFERENCE: Final = re.compile(r"^#\s*syntax=([^\s]+)", re.MULTILINE)
SECRET_FIELD: Final = re.compile(
    r"(authorization|credential|password|private.?key|secret|token)", re.IGNORECASE
)
PIP_TOOLS_VERSION: Final = "7.5.2"
EXPECTED_RUNTIME_USER: Final = "10001:10001"
VENDOR_SERVICES: Final = ("portal-postgres", "portal-redis", "portal-keycloak")


@dataclass(frozen=True)
class ImageBuild:
    name: str
    dockerfile: Path
    first_tag: str
    final_tag: str
    source_revision: str
    build_args: tuple[tuple[str, str], ...]
    content_roots: tuple[str, ...]
    expected_entrypoint: tuple[str, ...]
    expected_command: tuple[str, ...]
    expected_ports: tuple[str, ...]
    forbidden_runtime_tools: tuple[str, ...]
    enforce_source_map_policy: bool = False


def run(
    command: list[str],
    *,
    capture: bool = True,
    check: bool = True,
) -> str:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def git_value(*arguments: str) -> str:
    return run(["git", *arguments])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def pinned_base_images(dockerfile: Path) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    known_stages: set[str] = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if not line.startswith("FROM "):
            continue
        parts = line.split()
        reference = parts[1]
        stage = parts[3] if len(parts) >= 4 and parts[2].upper() == "AS" else ""
        if reference in known_stages:
            images.append({"source_stage": reference, "stage": stage})
        elif not PINNED_REFERENCE.fullmatch(reference):
            raise ValueError(f"{dockerfile}: base image is not digest pinned: {reference}")
        else:
            images.append({"reference": reference, "stage": stage})
        if stage:
            known_stages.add(stage)
    if not images:
        raise ValueError(f"{dockerfile}: no base images found")
    return images


def pinned_frontend(dockerfile: Path) -> str:
    match = FRONTEND_REFERENCE.search(dockerfile.read_text(encoding="utf-8"))
    if match is None or not PINNED_REFERENCE.fullmatch(match.group(1)):
        raise ValueError(f"{dockerfile}: Dockerfile frontend is not digest pinned")
    return match.group(1)


def pinned_portal_actions(workflow: Path) -> list[dict[str, str]]:
    content = workflow.read_text(encoding="utf-8")
    portal_jobs = content.split("\n  quality:", maxsplit=1)[0]
    actions: list[dict[str, str]] = []
    for action, revision in ACTION_REFERENCE.findall(portal_jobs):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(f"{workflow}: Portal action is not SHA pinned: {action}@{revision}")
        actions.append({"action": action, "commit": revision})
    if not actions:
        raise ValueError(f"{workflow}: no Portal build actions found")
    return actions


def image_id(tag: str) -> str:
    return run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])


def pinned_vendor_images(compose: Path) -> list[dict[str, str]]:
    content = compose.read_text(encoding="utf-8")
    pinned: list[dict[str, str]] = []
    for service in VENDOR_SERVICES:
        block = re.search(
            rf"^  {re.escape(service)}:\s*$"
            rf"(?P<body>.*?)(?=^  [a-z0-9][a-z0-9-]*:\s*$|^networks:\s*$|^volumes:\s*$|\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if block is None:
            raise ValueError(f"{compose}: missing vendor service: {service}")
        image = re.search(r"^\s{4}image:\s*(\S+)\s*$", block.group("body"), re.MULTILINE)
        if image is None or not PINNED_REFERENCE.fullmatch(image.group(1)):
            reference = image.group(1) if image is not None else "<missing>"
            raise ValueError(f"{compose}: vendor image is not digest pinned: {service}={reference}")
        pinned.append({"service": service, "reference": image.group(1)})
    return pinned


def _image_config(tag: str) -> dict[str, object]:
    rendered = run(["docker", "image", "inspect", "--format", "{{json .Config}}", tag])
    parsed = json.loads(rendered)
    if not isinstance(parsed, dict):
        raise ValueError(f"{tag}: invalid image configuration")
    return parsed


def inspect_source_maps(tag: str) -> int:
    script = r"""
const fs = require("fs");
const path = require("path");
let count = 0;
const unsafe = [];
function visit(value, file) {
  if (Array.isArray(value)) {
    for (const item of value) visit(item, file);
    return;
  }
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value.sourceContent) && value.sourceContent.some(Boolean)) {
    unsafe.push(`${file}:embedded-source-content`);
  }
  if (Array.isArray(value.sourcesContent) && value.sourcesContent.some(Boolean)) {
    unsafe.push(`${file}:embedded-sources-content`);
  }
  if (Array.isArray(value.sources)) {
    if (value.sources.length) unsafe.push(`${file}:source-bearing-map`);
    for (const source of value.sources) {
      if (typeof source === "string" &&
          (source.startsWith("/") || /^[A-Za-z]:[\\/]/.test(source))) {
        unsafe.push(`${file}:absolute-source-path`);
      }
    }
  }
  if (typeof value.mappings === "string" && value.mappings.length) {
    unsafe.push(`${file}:source-bearing-mappings`);
  }
  for (const child of Object.values(value)) visit(child, file);
}
function walk(root) {
  for (const entry of fs.readdirSync(root, {withFileTypes: true})) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) walk(target);
    else if (entry.isFile() && entry.name.endsWith(".map")) {
      count += 1;
      const raw = fs.readFileSync(target, "utf8");
      if (/BEGIN [A-Z ]*PRIVATE KEY|client[_-]?secret|password\s*[:=]/i.test(raw)) {
        unsafe.push(`${target}:sensitive-marker`);
      }
      try { visit(JSON.parse(raw), target); }
      catch { unsafe.push(`${target}:invalid-json`); }
    }
  }
}
walk("/app");
if (unsafe.length) {
  console.error(unsafe.sort().join("\n"));
  process.exit(1);
}
process.stdout.write(String(count));
"""
    output = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--entrypoint",
            "node",
            tag,
            "-e",
            script,
        ]
    )
    return int(output)


def inspect_image(image: ImageBuild) -> dict[str, object]:
    tag = image.final_tag
    config = _image_config(tag)
    environment = config.get("Env") or []
    if not isinstance(environment, list):
        raise ValueError(f"{tag}: image environment is not a list")
    sensitive_names = [
        entry.split("=", maxsplit=1)[0]
        for entry in environment
        if isinstance(entry, str) and SECRET_FIELD.search(entry.split("=", maxsplit=1)[0])
    ]
    if sensitive_names:
        names = ", ".join(sorted(sensitive_names))
        raise ValueError(f"{tag}: sensitive environment field(s) embedded: {names}")

    forbidden = (
        "/app/.env /app/.git /app/.github /app/tests /app/test "
        "/app/playwright-report /app/test-results /app/.pytest_cache "
        "/app/.mypy_cache /app/.ruff_cache /root/.cache/pip /root/.cache/pnpm"
    )
    roots = " ".join(image.content_roots)
    tools = " ".join(image.forbidden_runtime_tools)
    inspection_command = (
        f"for root in {roots}; do "
        'test -d "$root"; '
        'test -z "$(find "$root" -xdev ! -uid 0 -print -quit)"; '
        'test -z "$(find "$root" -xdev -type f -perm -0022 -print -quit)"; '
        'test -z "$(find "$root" -xdev -type d -perm -0022 -print -quit)"; '
        "done; "
        'test -z "$(find /bin /sbin /usr/bin /usr/sbin -xdev -type f '
        '\\( -perm -4000 -o -perm -2000 \\) -print -quit 2>/dev/null)"; '
        f"for tool in {tools}; do "
        'if command -v "$tool" >/dev/null 2>&1; then '
        'echo "unexpected runtime tool: $tool" >&2; exit 1; fi; done; '
        f'for path in {forbidden}; do test ! -e "$path"; done'
    )
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--entrypoint",
            "sh",
            tag,
            "-ec",
            inspection_command,
        ]
    )
    user = config.get("User") or ""
    if user != EXPECTED_RUNTIME_USER:
        raise ValueError(
            f"{tag}: expected runtime user {EXPECTED_RUNTIME_USER}, observed {user or '<unset>'}"
        )
    entrypoint = tuple(config.get("Entrypoint") or ())
    command = tuple(config.get("Cmd") or ())
    ports = tuple(sorted((config.get("ExposedPorts") or {}).keys()))
    if entrypoint != image.expected_entrypoint:
        raise ValueError(f"{tag}: unexpected entrypoint: {entrypoint}")
    if command != image.expected_command:
        raise ValueError(f"{tag}: unexpected command: {command}")
    if ports != image.expected_ports:
        raise ValueError(f"{tag}: unexpected exposed ports: {ports}")

    source_map_count = inspect_source_maps(tag) if image.enforce_source_map_policy else 0
    return {
        "non_root_user": user,
        "root_owned_application": True,
        "runtime_application_writable": False,
        "forbidden_paths_absent": True,
        "sensitive_env_absent": True,
        "setid_files_absent": True,
        "forbidden_runtime_tools_absent": list(image.forbidden_runtime_tools),
        "entrypoint": list(entrypoint),
        "command": list(command),
        "exposed_ports": list(ports),
        "safe_source_maps": source_map_count,
    }


def application_hashes(tag: str, roots: tuple[str, ...]) -> dict[str, str]:
    command = (
        "find " + " ".join(roots) + (" -xdev -type f -exec sha256sum '{}' ';' 2>/dev/null | sort")
    )
    output = run(["docker", "run", "--rm", "--entrypoint", "sh", tag, "-ec", command])
    hashes: dict[str, str] = {}
    for line in output.splitlines():
        digest, path = line.split(maxsplit=1)
        hashes[path] = digest
    return hashes


def build(image: ImageBuild, tag: str) -> str:
    command = [
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--platform",
        PLATFORM,
        "--provenance=false",
        "--sbom=false",
        "--file",
        image.dockerfile.as_posix(),
        "--tag",
        tag,
        "--label",
        f"org.opencontainers.image.revision={image.source_revision}",
    ]
    for name, value in image.build_args:
        command.extend(["--build-arg", f"{name}={value}"])
    command.append(".")
    run(command, capture=False)
    return image_id(tag)


def manifest_has_sensitive_data(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_FIELD.search(key):
                raise ValueError(f"manifest contains prohibited sensitive field at {path}.{key}")
            manifest_has_sensitive_data(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            manifest_has_sensitive_data(child, f"{path}[{index}]")
    elif isinstance(value, str) and re.search(r"(^[A-Za-z]:\\)|(^/home/)|(^/Users/)", value):
        raise ValueError(f"manifest contains a host-specific path at {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-identical", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.require_clean and git_value("status", "--porcelain"):
            raise ValueError("a clean Git worktree is required")

        source_commit = os.environ.get("GITHUB_SHA") or git_value("rev-parse", "HEAD")
        branch = os.environ.get("GITHUB_REF_NAME") or git_value("branch", "--show-current")
        source_epoch = int(git_value("show", "-s", "--format=%ct", source_commit))
        source_time = datetime.fromtimestamp(source_epoch, UTC).isoformat().replace("+00:00", "Z")
        workflow = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

        common_revision = ("PORTAL_BUILD_SHA", source_commit)
        images = (
            ImageBuild(
                name="portal-api",
                dockerfile=Path("apps/portal-api/Dockerfile"),
                first_tag="fintech-portal-api:s06-01-clean-a",
                final_tag="fintech-portal-api:local",
                source_revision=source_commit,
                build_args=(
                    ("PORTAL_API_VERSION", "0.1.0"),
                    ("PORTAL_API_BUILD_SHA", source_commit),
                    ("PORTAL_API_BUILD_TIME", source_time),
                    ("SOURCE_DATE_EPOCH", str(source_epoch)),
                ),
                content_roots=("/app", "/usr/local/lib/python3.11/site-packages"),
                expected_entrypoint=(),
                expected_command=(
                    "uvicorn",
                    "portal_api.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8010",
                    "--no-access-log",
                ),
                expected_ports=("8010/tcp", "9464/tcp"),
                forbidden_runtime_tools=("pip", "pip3", "gcc", "cc", "make"),
            ),
            ImageBuild(
                name="portal-web",
                dockerfile=Path("apps/portal-web/Dockerfile"),
                first_tag="fintech-portal-web:s06-01-clean-a",
                final_tag="fintech-payments-data-platform-portal-web:latest",
                source_revision=source_commit,
                build_args=(
                    ("PORTAL_WEB_VERSION", "0.1.0"),
                    common_revision,
                    ("PORTAL_ENV", "local"),
                    ("PORTAL_API_INTERNAL_URL", "http://portal-api:8010"),
                    ("PORTAL_IDP_PUBLIC_URL", "http://portal-idp.localhost:8081"),
                    ("SOURCE_DATE_EPOCH", str(source_epoch)),
                ),
                content_roots=("/app",),
                expected_entrypoint=("docker-entrypoint.sh",),
                expected_command=("node", "apps/portal-web/server.js"),
                expected_ports=("3000/tcp",),
                forbidden_runtime_tools=(
                    "npm",
                    "npx",
                    "corepack",
                    "yarn",
                    "yarnpkg",
                    "gcc",
                    "cc",
                    "make",
                ),
                enforce_source_map_policy=True,
            ),
        )

        image_results: list[dict[str, object]] = []
        identical = True
        nondeterminism: list[dict[str, object]] = []
        for image in images:
            first_digest = build(image, image.first_tag)
            second_digest = build(image, image.final_tag)
            image_identical = first_digest == second_digest
            identical = identical and image_identical
            first_hashes = application_hashes(image.first_tag, image.content_roots)
            second_hashes = application_hashes(image.final_tag, image.content_roots)
            changed_paths = sorted(
                path
                for path in first_hashes.keys() | second_hashes.keys()
                if first_hashes.get(path) != second_hashes.get(path)
            )
            content_identical = not changed_paths
            if not image_identical:
                if image.name == "portal-web" and any(
                    "prerender-manifest" in path or "server-reference-manifest" in path
                    for path in changed_paths
                ):
                    reason = (
                        "Next.js generates per-build preview and server-action cryptographic "
                        "material; deterministic secret injection is outside S06-01"
                    )
                elif content_identical:
                    reason = (
                        "local Docker exporter layer metadata differs despite identical "
                        "application and dependency file content"
                    )
                else:
                    reason = "application file content differs between clean builds"
                nondeterminism.append(
                    {
                        "image": image.name,
                        "reason": reason,
                        "changed_application_paths": changed_paths,
                    }
                )
            inspection = inspect_image(image)
            dockerfile = REPOSITORY_ROOT / image.dockerfile
            image_results.append(
                {
                    "name": image.name,
                    "platform": PLATFORM,
                    "context": ".",
                    "dockerfile": {
                        "path": image.dockerfile.as_posix(),
                        "sha256": sha256_file(dockerfile),
                        "frontend": pinned_frontend(dockerfile),
                    },
                    "base_images": pinned_base_images(dockerfile),
                    "output": {
                        "reference": image.final_tag,
                        "digest": second_digest,
                    },
                    "clean_builds": {
                        "first_digest": first_digest,
                        "second_digest": second_digest,
                        "identical": image_identical,
                        "application_content_identical": content_identical,
                        "changed_application_paths": changed_paths,
                    },
                    "inspection": inspection,
                }
            )

        dependency_inputs = [
            "apps/portal-api/requirements.lock",
            "apps/portal-api/requirements-dev.lock",
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
        ]
        manifest: dict[str, object] = {
            "schema_version": "portal-artifact-build/v1",
            "repository": {
                "name": REPOSITORY_ROOT.name,
                "branch": branch,
                "source_commit": source_commit,
            },
            "workflow": {
                "path": ".github/workflows/ci.yml",
                "sha256": sha256_file(workflow),
                "actions": pinned_portal_actions(workflow),
            },
            "vendor_images": pinned_vendor_images(REPOSITORY_ROOT / "docker-compose.yml"),
            "dependency_locks": [
                {"path": path, "sha256": sha256_file(REPOSITORY_ROOT / path)}
                for path in dependency_inputs
            ],
            "images": image_results,
            "build_tools": {
                "docker": run(["docker", "version", "--format", "{{.Client.Version}}"]),
                "buildx": run(["docker", "buildx", "version"]),
                "python_lock_compiler": f"pip-tools=={PIP_TOOLS_VERSION}",
                "pnpm": "11.9.0",
            },
            "reproducibility": {
                "repository_inputs": "immutable",
                "oci_image_identity": "verified" if identical else "not_verified",
                "source_date_epoch": source_epoch,
                "blocking_nondeterminism": nondeterminism,
            },
            "metadata": {
                "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "timestamp_affects_image_identity": False,
            },
        }
        manifest_has_sensitive_data(manifest)
        output = args.output if args.output.is_absolute() else REPOSITORY_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Portal artifact manifest: {output}")
        for result in image_results:
            builds = result["clean_builds"]
            assert isinstance(builds, dict)
            print(
                f"{result['name']}: {builds['first_digest']} / "
                f"{builds['second_digest']} identical={builds['identical']}"
            )
        if not identical and args.require_identical:
            raise ValueError("clean builds did not produce identical OCI image identities")
        if not identical:
            print("OCI byte identity remains NOT VERIFIED; see blocking_nondeterminism in manifest")
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Portal artifact build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
