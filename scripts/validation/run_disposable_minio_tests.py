"""Run the real MinIO/Silver integration suite in an owned disposable environment.

The launcher renders a Compose override in the operating-system temporary
directory.  It replaces the repository's persistent MinIO data mount with one
run-owned volume while preserving the canonical application bucket contract.
Every resource is checked for exact ownership before it is used or removed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
BASE_COMPOSE_FILE: Final = REPOSITORY_ROOT / "docker-compose.yml"
PERSISTENT_MINIO_VOLUME: Final = "fintech-payments-minio-data"
PHASE: Final = "ff-06c"
REPOSITORY_ID: Final = "fintech-payments-data-platform"
CANONICAL_BUCKETS: Final = {
    "MINIO_BRONZE_BUCKET": "fintech-bronze",
    "MINIO_QUARANTINE_BUCKET": "fintech-quarantine",
    "MINIO_SILVER_BUCKET": "fintech-silver",
}
REQUIRED_LABELS: Final = {
    "com.fintech.validation": "true",
    "com.fintech.validation.phase": PHASE,
    "com.fintech.validation.repository": REPOSITORY_ID,
}
PROTECTED_PATHS: Final = frozenset(
    {
        ".claude/settings.json",
        "docs/implementation-prompts/prd-001-dataset-bootstrap-activation.md",
        "docs/implementation-prompts/prd-001-focused-verification.md",
        "docs/implementation-prompts/prd-002-cdc-key-only-delete.md",
        "docs/implementation-prompts/prd-003-cross-system-recovery.md",
        "docs/implementation-prompts/prd-004-runtime-least-privilege.md",
        "docs/implementation-prompts/prd-005-versioned-database-migrations.md",
        "docs/implementation-prompts/production-pilot-review.md",
        "fintech-payments-data-platform.code-workspace",
    }
)
RUN_ID_PATTERN: Final = re.compile(r"^ff06c-minio-[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}$")


class IsolationError(RuntimeError):
    """Fail-closed validation error containing no secret material."""


@dataclass(frozen=True)
class RunContext:
    """Immutable identity for one disposable validation run."""

    run_id: str
    project_name: str
    volume_name: str
    network_name: str
    temporary_directory: Path
    override_path: Path
    api_port: int
    console_port: int

    @classmethod
    def create(cls) -> RunContext:
        timestamp = time.strftime("%Y%m%dt%H%M%Sz", time.gmtime())
        run_id = f"ff06c-minio-{timestamp}-{secrets.token_hex(4)}"
        validate_run_id(run_id)
        temporary_directory = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))
        override_path = temporary_directory / "compose.override.yml"
        return cls(
            run_id=run_id,
            project_name=run_id,
            volume_name=f"{run_id}-data",
            network_name=f"{run_id}-network",
            temporary_directory=temporary_directory,
            override_path=override_path,
            api_port=reserve_free_port(),
            console_port=reserve_free_port(),
        )

    @property
    def labels(self) -> dict[str, str]:
        return {**REQUIRED_LABELS, "com.fintech.validation.run-id": self.run_id}


@dataclass(frozen=True)
class PersistentVolumeSnapshot:
    """Docker-metadata-only snapshot of the portfolio MinIO volume."""

    exists: bool
    name: str | None
    driver: str | None
    scope: str | None
    labels: tuple[tuple[str, str], ...]
    options: tuple[tuple[str, str], ...]
    attached_containers: tuple[str, ...]


def validate_run_id(run_id: str) -> None:
    """Reject unsafe, unbounded, or non-Docker run identities."""

    if not RUN_ID_PATTERN.fullmatch(run_id) or len(run_id) > 63:
        raise IsolationError("FF06C_RUN_ID_INVALID")


def reserve_free_port() -> int:
    """Ask the operating system for an unused loopback port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def validate_temporary_path(path: Path, *, repository_root: Path = REPOSITORY_ROOT) -> None:
    """Require run files to remain outside the repository working tree."""

    resolved = path.resolve()
    root = repository_root.resolve()
    if resolved == root or root in resolved.parents:
        raise IsolationError("FF06C_TEMP_PATH_INSIDE_REPOSITORY")


def validate_tracked_inputs(paths: Sequence[str], *, tracked: frozenset[str]) -> None:
    """Reject protected, untracked, or duplicated validator inputs."""

    normalized = tuple(path.replace("\\", "/") for path in paths)
    if len(normalized) != len(set(normalized)):
        raise IsolationError("FF06C_DUPLICATE_TRACKED_INPUT")
    if any(path in PROTECTED_PATHS for path in normalized):
        raise IsolationError("FF06C_PROTECTED_PATH_ENUMERATED")
    if any(path not in tracked for path in normalized):
        raise IsolationError("FF06C_UNTRACKED_INPUT")


def expected_labels(context: RunContext) -> dict[str, str]:
    """Return the exact custom ownership label contract."""

    return context.labels


def render_override(context: RunContext) -> str:
    """Generate the external Compose override without embedding credentials."""

    validate_temporary_path(context.override_path)
    labels = "\n".join(f'      {key}: "{value}"' for key, value in context.labels.items())
    resource_labels = "\n".join(f'      {key}: "{value}"' for key, value in context.labels.items())
    return f"""services:
  minio:
    labels:
{labels}
    ports: !override
      - "127.0.0.1:${{FF06C_MINIO_API_PORT:?}}:9000"
      - "127.0.0.1:${{FF06C_MINIO_CONSOLE_PORT:?}}:9001"
    volumes: !override
      - type: volume
        source: ff06c_minio_data
        target: /data
    networks: !override
      - ff06c_minio_network
  minio-init:
    labels:
{labels}
    networks: !override
      - ff06c_minio_network
volumes:
  minio_data: !reset null
  ff06c_minio_data:
    name: "${{FF06C_MINIO_VOLUME_NAME:?}}"
    labels:
{resource_labels}
networks:
  ff06c_minio_network:
    name: "${{FF06C_MINIO_NETWORK_NAME:?}}"
    labels:
{resource_labels}
"""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def validate_rendered_config(config: Mapping[str, Any], context: RunContext) -> None:
    """Prove the merged Compose model preserves contract and storage isolation."""

    serialized = json.dumps(config, sort_keys=True)
    if PERSISTENT_MINIO_VOLUME in serialized:
        raise IsolationError("FF06C_PERSISTENT_VOLUME_IN_EFFECTIVE_CONFIG")

    services = _mapping(config.get("services"))
    minio = _mapping(services.get("minio"))
    initializer = _mapping(services.get("minio-init"))
    if not minio or not initializer:
        raise IsolationError("FF06C_REQUIRED_SERVICE_MISSING")

    mounts = [
        _mapping(item)
        for item in _sequence(minio.get("volumes"))
        if _mapping(item).get("target") == "/data"
    ]
    if len(mounts) != 1:
        raise IsolationError("FF06C_MINIO_DATA_MOUNT_COUNT_INVALID")
    if mounts[0].get("source") != "ff06c_minio_data":
        raise IsolationError("FF06C_MINIO_DATA_SOURCE_INVALID")

    volumes = _mapping(config.get("volumes"))
    volume = _mapping(volumes.get("ff06c_minio_data"))
    if volume.get("name") != context.volume_name:
        raise IsolationError("FF06C_RUN_VOLUME_NAME_INVALID")
    validate_label_mapping(_mapping(volume.get("labels")), context)

    networks = _mapping(config.get("networks"))
    network = _mapping(networks.get("ff06c_minio_network"))
    if network.get("name") != context.network_name:
        raise IsolationError("FF06C_RUN_NETWORK_NAME_INVALID")
    validate_label_mapping(_mapping(network.get("labels")), context)

    for service_name, service in (("minio", minio), ("minio-init", initializer)):
        validate_label_mapping(_mapping(service.get("labels")), context)
        service_networks = _mapping(service.get("networks"))
        if set(service_networks) != {"ff06c_minio_network"}:
            raise IsolationError(f"FF06C_{service_name.upper()}_NETWORK_INVALID")

    environment = _mapping(initializer.get("environment"))
    for key, expected in CANONICAL_BUCKETS.items():
        if environment.get(key) != expected:
            raise IsolationError("FF06C_CANONICAL_BUCKET_CONTRACT_CHANGED")


def validate_label_mapping(labels: Mapping[str, Any], context: RunContext) -> None:
    """Require every custom ownership label to match the current run."""

    for key, expected in expected_labels(context).items():
        if str(labels.get(key, "")) != expected:
            raise IsolationError("FF06C_OWNERSHIP_LABEL_MISMATCH")


def validate_resource_ownership(
    resource: Mapping[str, Any],
    context: RunContext,
    *,
    kind: str,
    expected_service: str | None = None,
) -> None:
    """Authorize a Docker resource for use or cleanup."""

    labels = _mapping(resource.get("Labels") or _mapping(resource.get("Config")).get("Labels"))
    validate_label_mapping(labels, context)
    if labels.get("com.docker.compose.project") != context.project_name:
        raise IsolationError(f"FF06C_FOREIGN_{kind.upper()}_PROJECT")
    if (
        expected_service is not None
        and labels.get("com.docker.compose.service") != expected_service
    ):
        raise IsolationError(f"FF06C_FOREIGN_{kind.upper()}_SERVICE")


def validate_minio_container(
    container: Mapping[str, Any],
    volume: Mapping[str, Any],
    context: RunContext,
) -> None:
    """Verify post-start container, endpoint, network, and volume ownership."""

    validate_resource_ownership(container, context, kind="container", expected_service="minio")
    validate_resource_ownership(volume, context, kind="volume")

    mounts = [
        _mapping(item)
        for item in _sequence(container.get("Mounts"))
        if _mapping(item).get("Destination") == "/data"
    ]
    if len(mounts) != 1 or mounts[0].get("Name") != context.volume_name:
        raise IsolationError("FF06C_POST_START_DATA_MOUNT_INVALID")
    if any(
        _mapping(item).get("Name") == PERSISTENT_MINIO_VOLUME
        for item in _sequence(container.get("Mounts"))
    ):
        raise IsolationError("FF06C_PERSISTENT_VOLUME_ATTACHED")

    networks = set(_mapping(_mapping(container.get("NetworkSettings")).get("Networks")))
    if networks != {context.network_name}:
        raise IsolationError("FF06C_POST_START_NETWORK_INVALID")

    ports = _mapping(_mapping(container.get("NetworkSettings")).get("Ports"))
    published = {
        int(_mapping(item).get("HostPort", 0))
        for binding in ports.values()
        for item in _sequence(binding)
    }
    if context.api_port not in published or context.console_port not in published:
        raise IsolationError("FF06C_ENDPOINT_OWNERSHIP_INVALID")


def assert_resource_absent(resources: Sequence[object], *, error_code: str) -> None:
    """Fail before startup when a run-owned identity already exists."""

    if resources:
        raise IsolationError(error_code)


def _run(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise IsolationError(
            f"FF06C_COMMAND_FAILED:{Path(arguments[0]).name}:{completed.returncode}"
        )
    return completed


def _docker_json(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    completed = _run(arguments, environment=environment)
    value = json.loads(completed.stdout)
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [dict(value)]
    raise IsolationError("FF06C_DOCKER_INSPECT_INVALID")


def _docker_lines(arguments: Sequence[str], *, environment: Mapping[str, str]) -> list[str]:
    completed = _run(arguments, environment=environment)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def inspect_optional(
    kind: str,
    name: str,
    *,
    environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    completed = _run(
        ["docker", kind, "inspect", name],
        environment=environment,
        check=False,
    )
    if completed.returncode != 0:
        return []
    value = json.loads(completed.stdout)
    return [dict(item) for item in value if isinstance(item, Mapping)]


def containers_using_volume(name: str, *, environment: Mapping[str, str]) -> tuple[str, ...]:
    lines = _docker_lines(
        ["docker", "ps", "-aq", "--filter", f"volume={name}"],
        environment=environment,
    )
    return tuple(sorted(lines))


def persistent_volume_snapshot(environment: Mapping[str, str]) -> PersistentVolumeSnapshot:
    records = inspect_optional("volume", PERSISTENT_MINIO_VOLUME, environment=environment)
    if not records:
        return PersistentVolumeSnapshot(False, None, None, None, (), (), ())
    record = records[0]
    labels = tuple(sorted((str(k), str(v)) for k, v in _mapping(record.get("Labels")).items()))
    options = tuple(sorted((str(k), str(v)) for k, v in _mapping(record.get("Options")).items()))
    return PersistentVolumeSnapshot(
        exists=True,
        name=str(record.get("Name")),
        driver=str(record.get("Driver")),
        scope=str(record.get("Scope")),
        labels=labels,
        options=options,
        attached_containers=containers_using_volume(
            PERSISTENT_MINIO_VOLUME,
            environment=environment,
        ),
    )


def compose_command(context: RunContext, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        context.project_name,
        "--file",
        str(BASE_COMPOSE_FILE),
        "--file",
        str(context.override_path),
        *arguments,
    ]


def build_environment(context: RunContext) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "FF06C_MINIO_API_PORT": str(context.api_port),
            "FF06C_MINIO_CONSOLE_PORT": str(context.console_port),
            "FF06C_MINIO_VOLUME_NAME": context.volume_name,
            "FF06C_MINIO_NETWORK_NAME": context.network_name,
            "MINIO_ACCESS_KEY": f"ff06c{secrets.token_hex(6)}",
            "MINIO_SECRET_KEY": secrets.token_urlsafe(32),
            "MINIO_ENDPOINT": f"http://127.0.0.1:{context.api_port}",
            "MINIO_SECURE": "false",
            "MINIO_REGION": "us-east-1",
            "RUN_MINIO_INTEGRATION": "1",
            "RUN_SILVER_INTEGRATION": "1",
            **CANONICAL_BUCKETS,
        }
    )
    return environment


def write_override(context: RunContext) -> None:
    validate_temporary_path(context.temporary_directory)
    context.override_path.write_text(render_override(context), encoding="utf-8", newline="\n")


def validate_prestart(context: RunContext, environment: Mapping[str, str]) -> dict[str, Any]:
    """Render Compose and prove no colliding or persistent resource can be used."""

    assert_resource_absent(
        inspect_optional("volume", context.volume_name, environment=environment),
        error_code="FF06C_RUN_VOLUME_PREEXISTS",
    )
    assert_resource_absent(
        inspect_optional("network", context.network_name, environment=environment),
        error_code="FF06C_RUN_NETWORK_PREEXISTS",
    )
    project_containers = _docker_lines(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={context.project_name}",
        ],
        environment=environment,
    )
    assert_resource_absent(
        project_containers,
        error_code="FF06C_COMPOSE_PROJECT_COLLISION",
    )
    rendered = _run(
        compose_command(context, "config", "--format", "json", "minio", "minio-init"),
        environment=environment,
    )
    config = json.loads(rendered.stdout)
    if not isinstance(config, Mapping):
        raise IsolationError("FF06C_RENDERED_CONFIG_INVALID")
    validate_rendered_config(config, context)
    return dict(config)


def inspect_started_resources(
    context: RunContext,
    environment: Mapping[str, str],
    *,
    persistent_before: PersistentVolumeSnapshot,
) -> dict[str, Any]:
    ids = _docker_lines(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={context.project_name}",
        ],
        environment=environment,
    )
    if len(ids) != 2:
        raise IsolationError("FF06C_PROJECT_CONTAINER_COUNT_INVALID")
    inspected = _docker_json(["docker", "inspect", *ids], environment=environment)
    by_service: dict[str, dict[str, Any]] = {}
    for item in inspected:
        labels = _mapping(_mapping(item.get("Config")).get("Labels"))
        service = str(labels.get("com.docker.compose.service", ""))
        validate_resource_ownership(item, context, kind="container", expected_service=service)
        if service not in {"minio", "minio-init"} or service in by_service:
            raise IsolationError("FF06C_PROJECT_CONTAINER_ROLE_INVALID")
        by_service[service] = item
    if set(by_service) != {"minio", "minio-init"}:
        raise IsolationError("FF06C_PROJECT_CONTAINER_ROLE_INVALID")

    volume_records = inspect_optional("volume", context.volume_name, environment=environment)
    network_records = inspect_optional("network", context.network_name, environment=environment)
    if len(volume_records) != 1 or len(network_records) != 1:
        raise IsolationError("FF06C_OWNED_RESOURCE_MISSING")
    validate_resource_ownership(volume_records[0], context, kind="volume")
    validate_resource_ownership(network_records[0], context, kind="network")
    validate_minio_container(by_service["minio"], volume_records[0], context)

    persistent_during = persistent_volume_snapshot(environment)
    if persistent_during != persistent_before:
        raise IsolationError("FF06C_PERSISTENT_VOLUME_METADATA_CHANGED")
    if any(container_id in ids for container_id in persistent_during.attached_containers):
        raise IsolationError("FF06C_PERSISTENT_VOLUME_ATTACHED")
    return {
        "container_count": len(inspected),
        "volume_count": 1,
        "network_count": 1,
        "minio_health": _mapping(by_service["minio"].get("State")).get("Health", {}).get("Status"),
    }


def run_integration_tests(
    context: RunContext,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    cache = context.temporary_directory / "pytest-cache"
    completed = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/minio/test_minio_settlement_storage.py",
            "tests/integration/silver/test_silver_processing.py",
            "-m",
            "minio_integration or silver_integration",
            "-o",
            f"cache_dir={cache}",
        ],
        environment=environment,
        timeout=600,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        print(output, file=sys.stderr)
        raise IsolationError("FF06C_MINIO_SILVER_TEST_FAILED")
    match = re.search(r"(?P<count>\d+) passed", output)
    if not match or int(match.group("count")) != 9:
        raise IsolationError("FF06C_MINIO_SILVER_TEST_COUNT_INVALID")
    return {"passed": 9, "previously_failing_test": "passed"}


def validate_cleanup_inventory(
    context: RunContext,
    environment: Mapping[str, str],
) -> dict[str, int]:
    containers = _docker_lines(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={context.project_name}",
        ],
        environment=environment,
    )
    volumes = inspect_optional("volume", context.volume_name, environment=environment)
    networks = inspect_optional("network", context.network_name, environment=environment)
    return {
        "containers": len(containers),
        "volumes": len(volumes),
        "networks": len(networks),
    }


def teardown_owned_resources(context: RunContext, environment: Mapping[str, str]) -> None:
    """Delete only resources whose ownership matches the current run."""

    ids = _docker_lines(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={context.project_name}",
        ],
        environment=environment,
    )
    if ids:
        for item in _docker_json(["docker", "inspect", *ids], environment=environment):
            labels = _mapping(_mapping(item.get("Config")).get("Labels"))
            service = str(labels.get("com.docker.compose.service", ""))
            validate_resource_ownership(item, context, kind="container", expected_service=service)
            if service not in {"minio", "minio-init"}:
                raise IsolationError("FF06C_FOREIGN_CONTAINER_CLEANUP_REFUSED")

    volume_records = inspect_optional("volume", context.volume_name, environment=environment)
    network_records = inspect_optional("network", context.network_name, environment=environment)
    for item in volume_records:
        validate_resource_ownership(item, context, kind="volume")
    for item in network_records:
        validate_resource_ownership(item, context, kind="network")

    _run(
        compose_command(context, "down", "--remove-orphans", "--timeout", "15"),
        environment=environment,
        timeout=120,
    )
    remaining_volume = inspect_optional("volume", context.volume_name, environment=environment)
    for item in remaining_volume:
        validate_resource_ownership(item, context, kind="volume")
    if remaining_volume:
        _run(["docker", "volume", "rm", context.volume_name], environment=environment)
    remaining_network = inspect_optional("network", context.network_name, environment=environment)
    for item in remaining_network:
        validate_resource_ownership(item, context, kind="network")
    if remaining_network:
        _run(["docker", "network", "rm", context.network_name], environment=environment)


def execute() -> dict[str, Any]:
    context = RunContext.create()
    environment = build_environment(context)
    persistent_before = persistent_volume_snapshot(environment)
    test_result: dict[str, Any] | None = None
    isolation: dict[str, Any] | None = None
    error: BaseException | None = None
    try:
        write_override(context)
        validate_prestart(context, environment)
        _run(
            compose_command(
                context,
                "up",
                "-d",
                "--wait",
                "--pull",
                "never",
                "--no-build",
                "minio",
            ),
            environment=environment,
            timeout=180,
        )
        _run(
            compose_command(context, "up", "--pull", "never", "--no-build", "minio-init"),
            environment=environment,
            timeout=180,
        )
        isolation = inspect_started_resources(
            context,
            environment,
            persistent_before=persistent_before,
        )
        test_result = run_integration_tests(context, environment)
    except BaseException as caught:
        error = caught
    finally:
        try:
            teardown_owned_resources(context, environment)
        except BaseException as cleanup_error:
            if error is None:
                error = cleanup_error
        persistent_after = persistent_volume_snapshot(environment)
        cleanup = validate_cleanup_inventory(context, environment)
        temporary_removed = False
        try:
            shutil.rmtree(context.temporary_directory)
            temporary_removed = True
        except OSError:
            temporary_removed = False

    if persistent_after != persistent_before:
        error = error or IsolationError("FF06C_PERSISTENT_VOLUME_METADATA_CHANGED")
    if any(cleanup.values()):
        error = error or IsolationError("FF06C_DISPOSABLE_RESOURCE_LEAK")
    if error is not None:
        if isinstance(error, IsolationError):
            raise error
        raise IsolationError("FF06C_UNEXPECTED_FAILURE") from error

    assert test_result is not None
    assert isolation is not None
    return {
        "schema_version": "ff06c-minio-isolation-run/v1",
        "result": "passed",
        "run_id": context.run_id,
        "project_name": context.project_name,
        "volume_name": context.volume_name,
        "network_name": context.network_name,
        "temporary_location": "OS temporary directory",
        "temporary_files_removed": temporary_removed,
        "canonical_buckets": dict(CANONICAL_BUCKETS),
        "persistent_volume": {
            "exists": persistent_before.exists,
            "metadata_unchanged": persistent_after == persistent_before,
            "mounted_by_run": False,
            "deleted": False,
        },
        "isolation": isolation,
        "tests": test_result,
        "cleanup": cleanup,
    }


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the sanitized run report as JSON.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    try:
        result = execute()
    except IsolationError as error:
        print(
            json.dumps({"result": "failed", "error": str(error)}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    if options.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("Disposable MinIO/Silver validation: 9 passed")
        print("Persistent portfolio MinIO volume mounted: NO")
        print("Disposable resources remaining: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
