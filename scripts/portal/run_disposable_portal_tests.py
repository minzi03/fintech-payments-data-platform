#!/usr/bin/env python3
"""Run Portal database tests against a uniquely owned disposable PostgreSQL."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import secrets
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TEST_SUPPORT = ROOT / "apps" / "portal-api" / "test_support"
sys.path.insert(0, str(TEST_SUPPORT))

from portal_test_support.disposable_database import (  # noqa: E402
    DisposableDatabaseError,
    DisposableDatabaseIdentity,
    PostgresDisposableDatabaseProbe,
    create_destructive_grant,
    install_disposable_marker,
    validate_compose_project,
)

COMPOSE_FILE = ROOT / "docker-compose.migration-test.yml"
SERVICE = "portal-migration-postgres"
PORT_PATTERN = re.compile(r"^(?:127\.0\.0\.1|\[::1\]):(\d+)$")


@dataclass(frozen=True, slots=True)
class RunContext:
    suite: str
    run_id: str
    project: str
    database_name: str
    host_port: int
    admin_user: str
    admin_password: str = field(repr=False)
    migration_password: str = field(repr=False)
    runtime_password: str = field(repr=False)
    audit_password: str = field(repr=False)
    archive_password: str = field(repr=False)

    @property
    def purpose(self) -> str:
        return f"portal-{self.suite}-validation"


def _safe_secret() -> str:
    return secrets.token_urlsafe(24)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _context(suite: str) -> RunContext:
    run_id = uuid4().hex[:16]
    return RunContext(
        suite=suite,
        run_id=run_id,
        project=f"portal-{suite}-test-{run_id}",
        database_name=f"portal_{suite}_test_{run_id}",
        host_port=_free_loopback_port(),
        admin_user=f"portal_test_admin_{run_id}",
        admin_password=_safe_secret(),
        migration_password=_safe_secret(),
        runtime_password=_safe_secret(),
        audit_password=_safe_secret(),
        archive_password=_safe_secret(),
    )


def _compose(
    context: RunContext,
    *arguments: str,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PORTAL_TEST_ADMIN_USER": context.admin_user,
            "PORTAL_TEST_ADMIN_PASSWORD": context.admin_password,
            "PORTAL_TEST_DATABASE_NAME": context.database_name,
            "PORTAL_TEST_DATABASE_PORT": str(context.host_port),
            "PORTAL_TEST_MIGRATION_PASSWORD": context.migration_password,
            "PORTAL_TEST_RUNTIME_PASSWORD": context.runtime_password,
            "PORTAL_TEST_AUDIT_PASSWORD": context.audit_password,
            "PORTAL_TEST_ARCHIVE_PASSWORD": context.archive_password,
        }
    )
    return subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            context.project,
            "--file",
            str(COMPOSE_FILE),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=capture,
        text=True,
    )


def _published_port(context: RunContext) -> int:
    result = _compose(context, "port", SERVICE, "5432", capture=True)
    endpoint = result.stdout.strip()
    match = PORT_PATTERN.fullmatch(endpoint)
    if match is None:
        raise DisposableDatabaseError(
            "DISPOSABLE_PORT_INVALID",
            "isolated PostgreSQL did not publish a loopback-only port",
        )
    port = int(match.group(1))
    if port != context.host_port:
        raise DisposableDatabaseError(
            "DISPOSABLE_PORT_MISMATCH",
            "isolated PostgreSQL published an unexpected host port",
        )
    return port


def _url(role: str, password: str, port: int, database_name: str) -> str:
    return (
        f"postgresql+psycopg://{role}:{quote(password, safe='')}@127.0.0.1:{port}/{database_name}"
    )


def _persistent_portal_container() -> str:
    result = subprocess.run(
        ["docker", "compose", "ps", "--quiet", "portal-postgres"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _pytest_command(suite: str) -> list[str]:
    base = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "apps/portal-api/pyproject.toml",
    ]
    if suite == "migration":
        return [
            *base,
            "apps/portal-api/tests/migrations",
            "-m",
            "destructive_migration",
        ]
    return [
        *base,
        "apps/portal-api/tests/integration/test_api.py",
        "apps/portal-api/tests/integration/test_audit_ledger.py",
        "apps/portal-api/tests/integration/test_audit_outbox_worker.py",
        "apps/portal-api/tests/integration/test_callback_orchestration.py",
        "apps/portal-api/tests/integration/test_login_initiation.py",
        "-m",
        "not destructive_migration",
    ]


def _run_tests(
    context: RunContext,
    *,
    port: int,
    token: str,
) -> int:
    environment = os.environ.copy()
    environment.update(
        {
            "PORTAL_TEST_DATABASE_MODE": f"orchestrated-{context.suite}-v1",
            "PORTAL_TEST_RUN_ID": context.run_id,
            "PORTAL_TEST_COMPOSE_PROJECT": context.project,
            "PORTAL_TEST_DATABASE_NAME": context.database_name,
            "PORTAL_TEST_DESTRUCTIVE_TOKEN": token,
            "PORTAL_TEST_MIGRATION_DATABASE_URL": _url(
                "portal_migration",
                context.migration_password,
                port,
                context.database_name,
            ),
            "PORTAL_TEST_RUNTIME_DATABASE_URL": _url(
                "portal_runtime",
                context.runtime_password,
                port,
                context.database_name,
            ),
            "PORTAL_TEST_ARCHIVE_DATABASE_URL": _url(
                "portal_archive",
                context.archive_password,
                port,
                context.database_name,
            ),
        }
    )
    result = subprocess.run(_pytest_command(context.suite), cwd=ROOT, env=environment, check=False)
    return result.returncode


def _verify_consumed(identity: DisposableDatabaseIdentity) -> None:
    marker = PostgresDisposableDatabaseProbe().read_marker(identity.migration_url)
    if marker is None or marker.consumed_at is None:
        raise DisposableDatabaseError(
            "TOKEN_NOT_CONSUMED",
            "destructive token was not consumed by the validation process",
        )


def run(suite: str) -> int:
    context = _context(suite)
    validate_compose_project(context.project, context.run_id)
    persistent_container_before = _persistent_portal_container()
    started = False
    test_result = 1
    failure: BaseException | None = None
    try:
        print(
            f"Starting disposable Portal {suite} validation: "
            f"database={context.database_name} project={context.project}",
            flush=True,
        )
        _compose(context, "up", "--detach", "--wait", SERVICE)
        started = True
        port = _published_port(context)
        admin_url = _url(
            context.admin_user,
            context.admin_password,
            port,
            context.database_name,
        )
        identity = DisposableDatabaseIdentity(
            run_id=context.run_id,
            compose_project=context.project,
            database_name=context.database_name,
            purpose=context.purpose,
            migration_url=_url(
                "portal_migration",
                context.migration_password,
                port,
                context.database_name,
            ),
            runtime_url=_url(
                "portal_runtime",
                context.runtime_password,
                port,
                context.database_name,
            ),
            archive_url=_url(
                "portal_archive",
                context.archive_password,
                port,
                context.database_name,
            ),
        )
        prepared = create_destructive_grant(identity)
        install_disposable_marker(admin_url, prepared)
        test_result = _run_tests(context, port=port, token=prepared.token)
        _verify_consumed(identity)
    except BaseException as error:
        failure = error
        if started:
            with contextlib.suppress(subprocess.CalledProcessError):
                _compose(context, "logs", "--tail", "100", SERVICE)
    finally:
        if started:
            try:
                _compose(context, "down", "--volumes", "--remove-orphans")
            except BaseException as error:
                if failure is None:
                    failure = error
        persistent_container_after = _persistent_portal_container()
        if persistent_container_before != persistent_container_after and failure is None:
            failure = DisposableDatabaseError(
                "PERSISTENT_CONTAINER_CHANGED",
                "normal Portal PostgreSQL container identity changed during validation",
            )
    if failure is not None:
        if isinstance(failure, DisposableDatabaseError):
            print(str(failure), file=sys.stderr)
        elif isinstance(failure, subprocess.CalledProcessError):
            print(
                f"DISPOSABLE_COMMAND_FAILED: command exited with {failure.returncode}",
                file=sys.stderr,
            )
        else:
            print("DISPOSABLE_VALIDATION_FAILED: sanitized diagnostics retained", file=sys.stderr)
        return 1
    if test_result != 0:
        return test_result
    print(
        f"Disposable Portal {suite} validation completed and project teardown succeeded.",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("integration", "migration"), required=True)
    arguments = parser.parse_args()
    return run(arguments.suite)


if __name__ == "__main__":
    raise SystemExit(main())
