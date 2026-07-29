"""Fail-closed tests for disposable database ownership proof."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from portal_test_support.disposable_database import (
    CREATOR_IDENTITY,
    DatabaseConnectionIdentity,
    DestructiveOperationGrant,
    DisposableDatabaseError,
    DisposableDatabaseIdentity,
    DisposableDatabaseMarker,
    PreparedDestructiveGrant,
    assert_disposable_database,
    consume_destructive_grant,
    create_destructive_grant,
    validate_compose_project,
)
from sqlalchemy.engine import make_url

NOW = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
RUN_ID = "0123456789abcdef"
DATABASE_NAME = f"portal_migration_test_{RUN_ID}"
PROJECT = f"portal-migration-test-{RUN_ID}"


class FakeProbe:
    def __init__(
        self,
        marker: DisposableDatabaseMarker | None,
        *,
        authority_counts: dict[str, int] | None = None,
        connected_database: str | None = None,
        connected_roles: dict[str, str] | None = None,
        excessive_authority: bool = False,
    ) -> None:
        self.marker = marker
        self._authority_counts = authority_counts or {}
        self.connected_database = connected_database
        self.connected_roles = connected_roles or {}
        self.excessive_authority = excessive_authority
        self.consume_calls = 0

    def connection_identity(self, url: str) -> DatabaseConnectionIdentity:
        parsed = make_url(url)
        role = self.connected_roles.get(parsed.username or "", parsed.username or "")
        return DatabaseConnectionIdentity(
            database_name=self.connected_database or parsed.database or "",
            role_name=role,
            is_superuser=self.excessive_authority,
        )

    def read_marker(self, migration_url: str) -> DisposableDatabaseMarker | None:
        del migration_url
        return self.marker

    def authority_counts(self, migration_url: str) -> dict[str, int]:
        del migration_url
        return self._authority_counts

    def consume_marker(self, migration_url: str, run_id: str, consumed_at: datetime) -> bool:
        del migration_url
        self.consume_calls += 1
        if self.marker is None or self.marker.run_id != run_id or self.marker.consumed_at:
            return False
        self.marker = replace(self.marker, consumed_at=consumed_at)
        return True


def _url(role: str, database: str = DATABASE_NAME, host: str = "127.0.0.1") -> str:
    return f"postgresql+psycopg://{role}:not-logged@{host}:6543/{database}"


def _identity(**changes: object) -> DisposableDatabaseIdentity:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "compose_project": PROJECT,
        "database_name": DATABASE_NAME,
        "purpose": "portal-migration-validation",
        "migration_url": _url("portal_migration"),
        "runtime_url": _url("portal_runtime"),
        "archive_url": _url("portal_archive"),
    }
    values.update(changes)
    return DisposableDatabaseIdentity(**values)  # type: ignore[arg-type]


def _prepared(identity: DisposableDatabaseIdentity | None = None) -> PreparedDestructiveGrant:
    return create_destructive_grant(identity or _identity(), now=NOW)


def _assert_code(code: str, operation) -> None:
    with pytest.raises(DisposableDatabaseError) as captured:
        operation()
    assert captured.value.code == code
    assert "not-logged" not in str(captured.value)


def _consume(
    identity: DisposableDatabaseIdentity,
    prepared: PreparedDestructiveGrant,
    probe: FakeProbe,
    *,
    token: str | None = None,
    environment: dict[str, str] | None = None,
) -> DestructiveOperationGrant:
    return consume_destructive_grant(
        identity,
        prepared.token if token is None else token,
        probe,
        environment=environment or {},
        now=NOW,
    )


def test_valid_grant_is_consumed_once_without_exposing_token() -> None:
    identity = _identity()
    prepared = _prepared(identity)
    probe = FakeProbe(prepared.marker)

    grant = _consume(identity, prepared, probe)

    assert grant.identity == identity
    assert probe.marker is not None
    assert probe.marker.consumed_at == NOW
    assert prepared.token not in repr(prepared)
    _assert_code(
        "TOKEN_ALREADY_CONSUMED",
        lambda: _consume(identity, prepared, probe),
    )


def test_missing_database_url_is_refused() -> None:
    identity = _identity(migration_url="")
    prepared = _prepared(identity)
    _assert_code(
        "DISPOSABLE_URL_MISSING",
        lambda: _consume(identity, prepared, FakeProbe(prepared.marker)),
    )


def test_arbitrary_database_url_is_refused() -> None:
    identity = _identity(migration_url=_url("portal_migration", "arbitrary_database"))
    prepared = _prepared(identity)
    _assert_code(
        "DATABASE_IDENTITY_MISMATCH",
        lambda: _consume(identity, prepared, FakeProbe(prepared.marker)),
    )


def test_missing_marker_is_refused() -> None:
    prepared = _prepared()
    _assert_code(
        "MARKER_MISSING",
        lambda: _consume(_identity(), prepared, FakeProbe(None)),
    )


def test_malformed_marker_is_refused() -> None:
    prepared = _prepared()
    malformed = replace(prepared.marker, destructive_nonce_hash="invalid")
    _assert_code(
        "MARKER_TOKEN_HASH_INVALID",
        lambda: _consume(_identity(), prepared, FakeProbe(malformed)),
    )


def test_expired_marker_and_token_are_refused() -> None:
    prepared = _prepared()
    expired = replace(prepared.marker, expires_at=NOW)
    _assert_code(
        "MARKER_EXPIRED",
        lambda: _consume(_identity(), prepared, FakeProbe(expired)),
    )


@pytest.mark.parametrize(
    ("marker_change", "code"),
    [
        ({"database_name": f"portal_migration_test_{'f' * 16}"}, "MARKER_DATABASE_MISMATCH"),
        ({"run_id": "f" * 16}, "MARKER_RUN_MISMATCH"),
        ({"creator_identity": "unknown"}, "MARKER_CREATOR_MISMATCH"),
        ({"purpose": "unapproved"}, "MARKER_PURPOSE_MISMATCH"),
    ],
)
def test_marker_identity_mismatch_is_refused(
    marker_change: dict[str, object],
    code: str,
) -> None:
    prepared = _prepared()
    marker = replace(prepared.marker, **marker_change)
    _assert_code(code, lambda: _consume(_identity(), prepared, FakeProbe(marker)))


def test_missing_and_incorrect_tokens_are_refused() -> None:
    prepared = _prepared()
    _assert_code(
        "TOKEN_MISSING",
        lambda: _consume(_identity(), prepared, FakeProbe(prepared.marker), token=""),
    )
    _assert_code(
        "TOKEN_MISMATCH",
        lambda: _consume(
            _identity(),
            prepared,
            FakeProbe(prepared.marker),
            token="incorrect-token",
        ),
    )


@pytest.mark.parametrize(
    "database_name",
    [
        "portal_control",
        "payments",
        "airflow",
        "airflow_metadata",
        "postgres",
        "template0",
        "template1",
        "keycloak",
    ],
)
def test_denylisted_database_names_are_refused(database_name: str) -> None:
    identity = _identity(
        database_name=database_name,
        migration_url=_url("portal_migration", database_name),
        runtime_url=_url("portal_runtime", database_name),
        archive_url=_url("portal_archive", database_name),
    )
    prepared = _prepared(identity)
    _assert_code(
        "PERSISTENT_DATABASE_DENIED",
        lambda: _consume(identity, prepared, FakeProbe(prepared.marker)),
    )


def test_configured_normal_portal_database_is_refused() -> None:
    prepared = _prepared()
    _assert_code(
        "PERSISTENT_DATABASE_DENIED",
        lambda: _consume(
            _identity(),
            prepared,
            FakeProbe(prepared.marker),
            environment={"PORTAL_API_DATABASE_URL": _url("portal_runtime")},
        ),
    )


def test_remote_host_is_refused() -> None:
    identity = _identity(migration_url=_url("portal_migration", host="db.example"))
    prepared = _prepared(identity)
    _assert_code(
        "REMOTE_DATABASE_DENIED",
        lambda: _consume(identity, prepared, FakeProbe(prepared.marker)),
    )


def test_role_database_and_connected_identity_mismatches_are_refused() -> None:
    identity = _identity(runtime_url=_url("portal_runtime", f"portal_migration_test_{'f' * 16}"))
    prepared = _prepared(identity)
    _assert_code(
        "DATABASE_IDENTITY_MISMATCH",
        lambda: _consume(identity, prepared, FakeProbe(prepared.marker)),
    )

    wrong_role = _identity(migration_url=_url("postgres"))
    prepared = _prepared(wrong_role)
    _assert_code(
        "DATABASE_ROLE_MISMATCH",
        lambda: _consume(wrong_role, prepared, FakeProbe(prepared.marker)),
    )

    prepared = _prepared()
    _assert_code(
        "CONNECTED_ROLE_MISMATCH",
        lambda: _consume(
            _identity(),
            prepared,
            FakeProbe(prepared.marker, connected_roles={"portal_runtime": "portal_migration"}),
        ),
    )
    _assert_code(
        "CONNECTED_DATABASE_MISMATCH",
        lambda: _consume(
            _identity(),
            prepared,
            FakeProbe(prepared.marker, connected_database="unexpected"),
        ),
    )
    _assert_code(
        "DATABASE_ROLE_EXCESSIVE_AUTHORITY",
        lambda: _consume(
            _identity(),
            prepared,
            FakeProbe(prepared.marker, excessive_authority=True),
        ),
    )


def test_preexisting_portal_authority_state_is_refused_without_cleanup() -> None:
    prepared = _prepared()
    probe = FakeProbe(prepared.marker, authority_counts={"portal_sessions": 1})
    _assert_code(
        "PREEXISTING_AUTHORITY_STATE",
        lambda: _consume(_identity(), prepared, probe),
    )
    assert probe.consume_calls == 0


def test_consumed_marker_can_be_asserted_after_migration_downgrade() -> None:
    identity = _identity()
    prepared = _prepared(identity)
    probe = FakeProbe(prepared.marker)
    _consume(identity, prepared, probe)

    marker = assert_disposable_database(
        identity,
        probe,
        environment={},
        now=NOW,
    )

    assert marker.consumed_at == NOW


def test_missing_marker_after_migration_downgrade_is_refused() -> None:
    _assert_code(
        "MARKER_MISSING",
        lambda: assert_disposable_database(
            _identity(),
            FakeProbe(None),
            environment={},
            now=NOW,
        ),
    )


@pytest.mark.parametrize(
    ("project", "run_id"),
    [
        ("", RUN_ID),
        ("portal-migration-test", RUN_ID),
        (f"portal-migration-test-{'f' * 16}", RUN_ID),
        ("fintech-payments-data-platform", RUN_ID),
    ],
)
def test_ambiguous_teardown_projects_are_refused(project: str, run_id: str) -> None:
    _assert_code("COMPOSE_PROJECT_SCOPE", lambda: validate_compose_project(project, run_id))


def test_ordinary_backend_command_excludes_destructive_migrations() -> None:
    repository = Path(__file__).resolve().parents[4]
    makefile = (repository / "Makefile").read_text(encoding="utf-8")
    migration_tests = (
        repository / "apps/portal-api/tests/migrations/test_portal_control_migrations.py"
    ).read_text(encoding="utf-8")

    assert '-m "not destructive_migration"' in makefile
    assert "--ignore=apps/portal-api/tests/integration/test_redis_abuse_enforcement.py" in makefile
    assert "pytest.mark.destructive_migration" in migration_tests
    assert "portal-migration-test:" in makefile


def test_repository_defaults_and_ci_do_not_supply_database_test_urls() -> None:
    repository = Path(__file__).resolve().parents[4]
    example = (repository / ".env.example").read_text(encoding="utf-8")
    workflow = (repository / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    disposable_compose = (repository / "docker-compose.migration-test.yml").read_text(
        encoding="utf-8"
    )

    assert "PORTAL_TEST_MIGRATION_DATABASE_URL=" not in example
    assert "PORTAL_TEST_RUNTIME_DATABASE_URL=" not in example
    assert "PORTAL_TEST_ARCHIVE_DATABASE_URL=" not in example
    assert "portal_control" not in workflow
    assert "run_disposable_portal_tests.py --suite migration" in workflow
    assert "run_disposable_portal_tests.py --suite integration" in workflow
    assert "/var/lib/postgresql/data" in disposable_compose
    assert "portal_postgres_data" not in disposable_compose


def test_database_fixtures_do_not_fallback_to_runtime_urls() -> None:
    repository = Path(__file__).resolve().parents[4]
    root_fixture = (repository / "tests/integration/conftest.py").read_text(encoding="utf-8")
    cdc_fixture = (repository / "tests/integration/cdc/conftest.py").read_text(encoding="utf-8")
    outbox_fixture = (
        repository / "apps/portal-api/tests/integration/test_audit_outbox_worker.py"
    ).read_text(encoding="utf-8")

    assert 'or os.getenv("DATABASE_URL")' not in root_fixture
    assert 'or os.getenv("DATABASE_URL")' not in cdc_fixture
    assert 'replace("portal_migration:", "portal_archive:")' not in outbox_fixture


def test_broad_portal_cleanup_is_guarded_by_session_ownership_proof() -> None:
    repository = Path(__file__).resolve().parents[4]
    conftest = (repository / "apps/portal-api/tests/conftest.py").read_text(encoding="utf-8")

    assert "autouse=True" in conftest
    assert "consume_destructive_grant(" in conftest
    assert "PORTAL_TEST_DATABASE_MODE" in conftest
    assert "PORTAL_TEST_DESTRUCTIVE_TOKEN" in conftest


def test_safe_errors_and_representations_do_not_contain_credentials_or_tokens() -> None:
    prepared = _prepared()
    assert prepared.token not in repr(prepared)
    identity = _identity()
    assert "not-logged" not in repr(identity)
    with pytest.raises(DisposableDatabaseError) as captured:
        _consume(identity, prepared, FakeProbe(None))
    assert "not-logged" not in str(captured.value)
    assert prepared.token not in str(captured.value)


def test_grant_lifetime_must_be_positive_and_bounded() -> None:
    _assert_code(
        "TOKEN_LIFETIME_INVALID",
        lambda: create_destructive_grant(_identity(), now=NOW, lifetime=timedelta(0)),
    )
    _assert_code(
        "TOKEN_LIFETIME_INVALID",
        lambda: create_destructive_grant(_identity(), now=NOW, lifetime=timedelta(hours=3)),
    )


def test_marker_creator_constant_is_stable() -> None:
    assert CREATOR_IDENTITY == "portal-test-orchestrator/v1"
