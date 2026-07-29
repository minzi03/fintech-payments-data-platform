"""Validate or regenerate the hash-locked Portal API dependency sets."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Final

from packaging.requirements import Requirement
from packaging.version import Version

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
PORTAL_ROOT: Final = REPOSITORY_ROOT / "apps" / "portal-api"
PYTHON_IMAGE: Final = (
    "python:3.11.15-slim-bookworm"
    "@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"
)
PIP_TOOLS_VERSION: Final = "7.5.2"
LOCKS: Final = (
    ("requirements.lock", False),
    ("requirements-dev.lock", True),
)
LOCK_PATTERN: Final = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+)")


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> dict[str, Version]:
    versions: dict[str, Version] = {}
    current_package: str | None = None
    current_has_hash = False

    for line in path.read_text(encoding="utf-8").splitlines():
        match = LOCK_PATTERN.match(line)
        if match:
            if current_package is not None and not current_has_hash:
                raise ValueError(f"{path}: {current_package} has no integrity hash")
            current_package = canonical_name(match.group(1))
            versions[current_package] = Version(match.group(2))
            current_has_hash = "--hash=sha256:" in line
        elif current_package is not None and "--hash=sha256:" in line:
            current_has_hash = True

    if current_package is not None and not current_has_hash:
        raise ValueError(f"{path}: {current_package} has no integrity hash")
    if not versions:
        raise ValueError(f"{path}: no locked packages found")
    return versions


def project_requirements(include_dev: bool) -> list[Requirement]:
    project = tomllib.loads((PORTAL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    raw_requirements = list(project["dependencies"])
    if include_dev:
        raw_requirements.extend(project["optional-dependencies"]["dev"])
    return [Requirement(value) for value in raw_requirements]


def validate_lock(path: Path, *, include_dev: bool) -> dict[str, Version]:
    locked = parse_lock(path)
    for requirement in project_requirements(include_dev):
        name = canonical_name(requirement.name)
        version = locked.get(name)
        if version is None:
            raise ValueError(f"{path}: direct dependency {requirement.name} is missing")
        if requirement.specifier and version not in requirement.specifier:
            raise ValueError(
                f"{path}: {requirement.name}=={version} violates {requirement.specifier}"
            )
    return locked


def check_locks() -> None:
    runtime = validate_lock(PORTAL_ROOT / "requirements.lock", include_dev=False)
    development = validate_lock(PORTAL_ROOT / "requirements-dev.lock", include_dev=True)
    mismatches = {
        name: (version, development.get(name))
        for name, version in runtime.items()
        if development.get(name) != version
    }
    if mismatches:
        details = ", ".join(
            f"{name}: runtime={runtime_version}, dev={dev_version}"
            for name, (runtime_version, dev_version) in sorted(mismatches.items())
        )
        raise ValueError(f"runtime/development lock drift: {details}")
    print(
        "Portal locks valid: "
        f"{len(runtime)} runtime packages, {len(development)} development packages"
    )


def docker_mount(source: Path, destination: str, *, read_only: bool = False) -> str:
    suffix = ":ro" if read_only else ""
    return f"{source.resolve()}:{destination}{suffix}"


def update_locks() -> None:
    with tempfile.TemporaryDirectory(prefix="portal-locks-") as temporary:
        output_dir = Path(temporary)
        compile_commands: list[str] = []
        for filename, include_dev in LOCKS:
            extra = "--extra=dev " if include_dev else ""
            compile_commands.append(
                "pip-compile --quiet --no-header --generate-hashes "
                "--no-emit-index-url --strip-extras --resolver=backtracking "
                f"{extra}--output-file=/output/{filename} pyproject.toml"
            )
        shell_script = " && ".join(
            [
                (
                    "python -m pip install --disable-pip-version-check --no-cache-dir "
                    f"pip-tools=={PIP_TOOLS_VERSION} >/dev/null"
                ),
                *compile_commands,
            ]
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                docker_mount(REPOSITORY_ROOT, "/workspace", read_only=True),
                "--volume",
                docker_mount(output_dir, "/output"),
                "--workdir",
                "/workspace/apps/portal-api",
                PYTHON_IMAGE,
                "sh",
                "-ec",
                shell_script,
            ],
            check=True,
        )
        for filename, _ in LOCKS:
            shutil.copyfile(output_dir / filename, PORTAL_ROOT / filename)
    check_locks()
    print(f"Portal locks regenerated with pip-tools {PIP_TOOLS_VERSION} in {PYTHON_IMAGE}")


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--update", action="store_true")
    args = parser.parse_args()

    try:
        if args.update:
            update_locks()
        else:
            check_locks()
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Portal dependency lock validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
