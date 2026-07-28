"""Verify the S06-04 Compose and running-container hardening contract safely."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
EXPECTED_USER: Final = "10001:10001"
FIRST_PARTY_SERVICES: Final = (
    "portal-api",
    "portal-audit-worker",
    "portal-migrate",
    "portal-web",
)
EXPECTED_NETWORKS: Final = {
    "portal-api": {"portal-app", "portal-data"},
    "portal-audit-worker": {"portal-data"},
    "portal-migrate": {"portal-data"},
    "portal-web": {"portal-app"},
    "portal-postgres": {"portal-data"},
    "portal-redis": {"portal-data"},
    "portal-keycloak": {"portal-data"},
}
EXPECTED_WORKER_ENVIRONMENT: Final = {
    "PORTAL_API_ABUSE_PROTECTION_ENABLED",
    "PORTAL_API_AUDIT_DEAD_LETTER_RETENTION_DAYS",
    "PORTAL_API_AUDIT_OUTBOX_BASE_BACKOFF_SECONDS",
    "PORTAL_API_AUDIT_OUTBOX_BATCH_SIZE",
    "PORTAL_API_AUDIT_OUTBOX_DELIVERY_TIMEOUT_SECONDS",
    "PORTAL_API_AUDIT_OUTBOX_DESTINATION",
    "PORTAL_API_AUDIT_OUTBOX_ENABLED",
    "PORTAL_API_AUDIT_OUTBOX_LEASE_SECONDS",
    "PORTAL_API_AUDIT_OUTBOX_MAX_ATTEMPTS",
    "PORTAL_API_AUDIT_OUTBOX_MAX_BACKOFF_SECONDS",
    "PORTAL_API_AUDIT_OUTBOX_POLL_INTERVAL_SECONDS",
    "PORTAL_API_AUDIT_OUTBOX_RETENTION_DAYS",
    "PORTAL_API_AUDIT_OUTBOX_WORKER_CONCURRENCY",
    "PORTAL_API_AUDIT_WORKER_DATABASE_URL",
    "PORTAL_API_BUILD_SHA",
    "PORTAL_API_BUILD_TIME",
    "PORTAL_API_ENVIRONMENT",
    "PORTAL_API_LOG_FORMAT",
    "PORTAL_API_LOG_LEVEL",
    "PORTAL_API_MAINTENANCE_BATCH_SIZE",
    "PORTAL_API_MAINTENANCE_ENABLED",
    "PORTAL_API_MAINTENANCE_INTERVAL_SECONDS",
    "PORTAL_API_MAINTENANCE_MAX_RUNTIME_SECONDS",
    "PORTAL_API_REPLAY_RETENTION_BUFFER_SECONDS",
    "PORTAL_API_SECRET_PROVIDER",
    "PORTAL_API_SECURITY_RUNTIME_ENABLED",
    "PORTAL_API_SERVICE_NAME",
    "PORTAL_API_SERVICE_VERSION",
    "PORTAL_API_TELEMETRY_ENABLED",
    "PORTAL_API_TELEMETRY_METRICS_EXPORTER",
    "PORTAL_API_TELEMETRY_TRACE_EXPORTER",
    "PORTAL_API_TERMINAL_ENVELOPE_RETENTION_DAYS",
}
EXPECTED_ROLE_ENVIRONMENTS: Final = {
    "portal-audit-worker": EXPECTED_WORKER_ENVIRONMENT,
    "portal-migrate": {"PORTAL_MIGRATION_DATABASE_URL"},
    "portal-web": {"PORTAL_API_INTERNAL_URL", "PORTAL_PUBLIC_ORIGIN"},
}
VENDOR_SERVICES: Final = ("portal-postgres", "portal-redis", "portal-keycloak")
PINNED_REFERENCE: Final = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def run(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def compose(*arguments: str) -> str:
    return run(["docker", "compose", "--env-file", ".env.example", *arguments])


def compose_configuration() -> dict[str, object]:
    rendered = compose("config", "--format", "json")
    parsed = json.loads(rendered)
    if not isinstance(parsed, dict):
        raise ValueError("PORTAL_HARDENING_INVALID_COMPOSE_CONFIG")
    return parsed


def _service(configuration: dict[str, object], name: str) -> dict[str, object]:
    services = configuration.get("services")
    if not isinstance(services, dict) or not isinstance(services.get(name), dict):
        raise ValueError(f"PORTAL_HARDENING_MISSING_SERVICE:{name}")
    return services[name]  # type: ignore[return-value]


def validate_compose_contract(configuration: dict[str, object]) -> None:
    for name in FIRST_PARTY_SERVICES:
        service = _service(configuration, name)
        if service.get("user") != EXPECTED_USER:
            raise ValueError(f"PORTAL_HARDENING_USER:{name}")
        if service.get("read_only") is not True:
            raise ValueError(f"PORTAL_HARDENING_READ_ONLY:{name}")
        if service.get("cap_drop") != ["ALL"]:
            raise ValueError(f"PORTAL_HARDENING_CAPABILITIES:{name}")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            raise ValueError(f"PORTAL_HARDENING_NNP:{name}")
        tmpfs = service.get("tmpfs") or []
        tmpfs_options = set(str(tmpfs[0]).split(",")) if len(tmpfs) == 1 else set()
        if (
            len(tmpfs) != 1
            or not str(tmpfs[0]).startswith("/tmp:")
            or not {"nosuid", "nodev", "noexec", "mode=1777"} <= tmpfs_options
            or not any(option.startswith("size=") for option in tmpfs_options)
        ):
            raise ValueError(f"PORTAL_HARDENING_TMPFS:{name}")
        if not isinstance(service.get("pids_limit"), int) or service["pids_limit"] <= 0:
            raise ValueError(f"PORTAL_HARDENING_PIDS:{name}")
        if not service.get("stop_grace_period"):
            raise ValueError(f"PORTAL_HARDENING_STOP_GRACE:{name}")
        logging = service.get("logging")
        if not isinstance(logging, dict) or logging.get("driver") != "json-file":
            raise ValueError(f"PORTAL_HARDENING_LOG_DRIVER:{name}")
        options = logging.get("options")
        if not isinstance(options, dict) or set(options) != {"max-file", "max-size"}:
            raise ValueError(f"PORTAL_HARDENING_LOG_BOUNDS:{name}")

    migration_health = _service(configuration, "portal-migrate").get("healthcheck")
    if not isinstance(migration_health, dict) or migration_health.get("disable") is not True:
        raise ValueError("PORTAL_HARDENING_MIGRATION_HEALTH")

    for name, expected in EXPECTED_NETWORKS.items():
        networks = _service(configuration, name).get("networks") or {}
        if not isinstance(networks, dict) or set(networks) != expected:
            raise ValueError(f"PORTAL_HARDENING_NETWORK_MEMBERSHIP:{name}")

    for name in VENDOR_SERVICES:
        image = _service(configuration, name).get("image")
        if not isinstance(image, str) or not PINNED_REFERENCE.fullmatch(image):
            raise ValueError(f"PORTAL_HARDENING_VENDOR_DIGEST:{name}")

    for name in (
        "portal-api",
        "portal-web",
        "portal-postgres",
        "portal-redis",
        "portal-keycloak",
    ):
        for port in _service(configuration, name).get("ports") or []:
            if not isinstance(port, dict) or port.get("host_ip") != "127.0.0.1":
                raise ValueError(f"PORTAL_HARDENING_HOST_BINDING:{name}")

    for name, expected in EXPECTED_ROLE_ENVIRONMENTS.items():
        environment = _service(configuration, name).get("environment") or {}
        if not isinstance(environment, dict) or set(environment) != expected:
            raise ValueError(f"PORTAL_HARDENING_ROLE_ENVIRONMENT:{name}")
    api_environment = _service(configuration, "portal-api").get("environment") or {}
    if not isinstance(api_environment, dict) or {
        "PORTAL_API_AUDIT_WORKER_DATABASE_URL",
        "PORTAL_MIGRATION_DATABASE_URL",
    } & set(api_environment):
        raise ValueError("PORTAL_HARDENING_API_ROLE_ENVIRONMENT")
    web_environment = _service(configuration, "portal-web").get("environment") or {}
    if any(
        re.search(r"(credential|password|private.?key|secret|token)", name, re.IGNORECASE)
        for name in web_environment
    ):
        raise ValueError("PORTAL_HARDENING_WEB_SECRET_EXPOSURE")


def container_id(service: str) -> str:
    identifier = compose("ps", "-a", "-q", service)
    if not identifier:
        raise ValueError(f"PORTAL_HARDENING_CONTAINER_MISSING:{service}")
    return identifier


def inspect_format(identifier: str, template: str) -> str:
    return run(["docker", "inspect", "--format", template, identifier])


def validate_running_container(service: str, expected_networks: set[str]) -> None:
    identifier = container_id(service)
    host = json.loads(inspect_format(identifier, "{{json .HostConfig}}"))
    configuration = compose_configuration()
    expected_service = _service(configuration, service)
    if host.get("ReadonlyRootfs") is not True:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_READ_ONLY:{service}")
    if host.get("CapDrop") != ["ALL"]:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_CAPABILITIES:{service}")
    if "no-new-privileges:true" not in (host.get("SecurityOpt") or []):
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_NNP_CONFIG:{service}")
    if host.get("PidsLimit") != expected_service.get("pids_limit"):
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_PIDS:{service}")
    expected_tmpfs = expected_service.get("tmpfs") or []
    if host.get("Tmpfs") != {"/tmp": str(expected_tmpfs[0]).split(":", maxsplit=1)[1]}:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_TMPFS:{service}")
    expected_logging = expected_service.get("logging")
    if not isinstance(expected_logging, dict):
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_LOGGING:{service}")
    if host.get("LogConfig") != {
        "Type": expected_logging.get("driver"),
        "Config": expected_logging.get("options"),
    }:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_LOGGING:{service}")
    expected_stop = str(expected_service.get("stop_grace_period"))
    actual_stop = inspect_format(identifier, "{{.Config.StopTimeout}}")
    if not expected_stop.endswith("s") or actual_stop != expected_stop.removesuffix("s"):
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_STOP_GRACE:{service}")

    networks = json.loads(inspect_format(identifier, "{{json .NetworkSettings.Networks}}"))
    network_catalog = configuration.get("networks")
    if not isinstance(network_catalog, dict):
        raise ValueError("PORTAL_HARDENING_NETWORK_CATALOG")
    actual_logical = {
        logical
        for logical, details in network_catalog.items()
        if isinstance(details, dict) and details.get("name") in networks
    }
    if actual_logical != expected_networks:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_NETWORKS:{service}")

    running_image = inspect_format(identifier, "{{.Image}}")
    service_image = compose("images", "-q", service)
    if service_image and not service_image.startswith("sha256:"):
        service_image = f"sha256:{service_image}"
    if running_image != service_image:
        raise ValueError(f"PORTAL_HARDENING_IMAGE_IDENTITY:{service}")

    if service == "portal-migrate":
        if inspect_format(identifier, "{{.State.ExitCode}}") != "0":
            raise ValueError("PORTAL_HARDENING_MIGRATION_EXIT")
        rendered_health = inspect_format(identifier, "{{json .Config.Healthcheck}}")
        health = json.loads(rendered_health)
        if health is not None and (not isinstance(health, dict) or health.get("Test") != ["NONE"]):
            raise ValueError("PORTAL_HARDENING_MIGRATION_HEALTH_RUNTIME")
        return

    identity = compose(
        "exec",
        "-T",
        service,
        "sh",
        "-ec",
        'printf \'%s:%s\' "$(id -u)" "$(id -g)"',
    )
    if identity != EXPECTED_USER:
        raise ValueError(f"PORTAL_HARDENING_RUNTIME_USER:{service}")
    status_check = (
        "awk '"
        "/^(CapPrm|CapEff|CapBnd|CapAmb):/"
        '{if ($2 != "0000000000000000") exit 1} '
        '/^NoNewPrivs:/{if ($2 != "1") exit 1; seen=1} '
        "END {if (!seen) exit 1}' /proc/1/status"
    )
    compose("exec", "-T", service, "sh", "-ec", status_check)


def _expect_dns_failure(service: str, host: str, runtime: str) -> None:
    if runtime == "node":
        script = (
            f"require('dns').promises.lookup('{host}')"
            ".then(()=>process.exit(1),()=>process.exit(0))"
        )
        compose("exec", "-T", service, "node", "-e", script)
    else:
        script = (
            "import socket,sys\n"
            f"try: socket.getaddrinfo('{host}', 1)\n"
            "except socket.gaierror: sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        compose("exec", "-T", service, "python", "-c", script)


def validate_network_paths() -> None:
    compose(
        "exec",
        "-T",
        "portal-web",
        "node",
        "-e",
        "fetch('http://portal-api:8010/health/live').then(r=>process.exit(r.ok?0:1))",
    )
    for host, port in (
        ("portal-postgres", 5432),
        ("portal-redis", 6379),
        ("portal-idp.localhost", 8081),
    ):
        compose(
            "exec",
            "-T",
            "portal-api",
            "python",
            "-c",
            f"import socket; socket.create_connection(('{host}', {port}), 2).close()",
        )
    compose(
        "exec",
        "-T",
        "portal-audit-worker",
        "python",
        "-c",
        "import socket; socket.create_connection(('portal-postgres', 5432), 2).close()",
    )

    for host in ("portal-postgres", "portal-redis", "portal-keycloak"):
        _expect_dns_failure("portal-web", host, "node")
    _expect_dns_failure("portal-audit-worker", "portal-web", "python")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Also validate running Portal containers and network paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        configuration = compose_configuration()
        validate_compose_contract(configuration)
        if args.runtime:
            for service, networks in EXPECTED_NETWORKS.items():
                if service in FIRST_PARTY_SERVICES:
                    validate_running_container(service, networks)
            validate_network_paths()
        print("Portal container hardening verification: PASS")
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as error:
        print(f"Portal container hardening verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
