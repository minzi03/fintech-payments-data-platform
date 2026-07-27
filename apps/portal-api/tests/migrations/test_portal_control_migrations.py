"""PostgreSQL migration and least-privilege integration tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from portal_api.db.migration_integrity import MIGRATION_LOCK_KEY, MigrationIntegrityError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

EXPECTED_TABLES = {
    "audit_archive_outbox",
    "oidc_login_transactions",
    "portal_capability_definitions",
    "portal_capability_overrides",
    "portal_policy_revisions",
    "portal_principals",
    "portal_login_intents",
    "portal_security_epochs",
    "portal_sessions",
    "portal_token_envelopes",
    "schema_migrations",
    "security_audit_events",
}


def _migration_url() -> str:
    value = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    if not value:
        pytest.skip("PORTAL_TEST_MIGRATION_DATABASE_URL is not configured")
    return value


def _runtime_url() -> str:
    value = os.environ.get("PORTAL_TEST_RUNTIME_DATABASE_URL", "")
    if not value:
        pytest.skip("PORTAL_TEST_RUNTIME_DATABASE_URL is not configured")
    return value


def _alembic_config(url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    os.environ["PORTAL_MIGRATION_DATABASE_URL"] = url
    return config


@pytest.mark.integration
def test_upgrade_downgrade_and_authoritative_history() -> None:
    url = _migration_url()
    config = _alembic_config(url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names(schema="portal_control")) == EXPECTED_TABLES
        with engine.connect() as connection:
            records = connection.execute(
                text(
                    "SELECT version, checksum, application_compat "
                    "FROM portal_control.schema_migrations ORDER BY version"
                )
            ).all()
            assert [record.version for record in records] == [
                "001_initial_portal_control",
                "002_login_intent_and_initiation",
                "003_callback_runtime_privileges",
            ]
            assert all(len(record.checksum) == 64 for record in records)
            assert all(record.application_compat == ">=0.1.0,<1.0.0" for record in records)
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.mark.integration
def test_runtime_role_cannot_read_history_or_execute_ddl() -> None:
    config = _alembic_config(_migration_url())
    command.upgrade(config, "head")
    engine = create_engine(_runtime_url())
    try:
        with engine.connect() as connection, pytest.raises(ProgrammingError):
            connection.execute(text("SELECT * FROM portal_control.schema_migrations"))
        with engine.connect() as connection, pytest.raises(ProgrammingError):
            connection.execute(text("CREATE TABLE portal_control.forbidden_runtime_ddl(id int)"))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_database_trigger_rejects_audit_mutation_with_frozen_sqlstate() -> None:
    url = _migration_url()
    config = _alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    event_id = str(uuid4())
    payload = {
        "event_id": event_id,
        "deduplication_key": str(uuid4()),
        "event_type": "auth.login_started.v1",
        "occurred_at": "2026-07-27T00:00:00+00:00",
        "actor_type": "ANONYMOUS",
        "correlation_id": "trigger-test-correlation",
        "request_id": "trigger-test-request",
        "outcome": "STARTED",
        "safe_metadata": {},
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT portal_control.append_security_audit_event(CAST(:payload AS jsonb))"),
                {"payload": json.dumps(payload)},
            )
        with pytest.raises(DBAPIError) as captured, engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.security_audit_events "
                    "SET outcome = 'MUTATED' WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            )
        assert captured.value.orig.sqlstate == "55000"
    finally:
        engine.dispose()


@pytest.mark.integration
def test_applied_migration_checksum_tampering_is_rejected() -> None:
    url = _migration_url()
    config = _alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            original = connection.execute(
                text(
                    "SELECT checksum FROM portal_control.schema_migrations "
                    "WHERE version = '001_initial_portal_control'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE portal_control.schema_migrations SET checksum = :checksum "
                    "WHERE version = '001_initial_portal_control'"
                ),
                {"checksum": "0" * 64},
            )
        with pytest.raises(MigrationIntegrityError, match="checksum does not match"):
            command.upgrade(config, "head")
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.schema_migrations SET checksum = :checksum "
                    "WHERE version = '001_initial_portal_control'"
                ),
                {"checksum": original},
            )
        engine.dispose()


@pytest.mark.integration
def test_concurrent_migration_runner_is_rejected_without_retry() -> None:
    url = _migration_url()
    config = _alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            lock_connection.commit()
            with pytest.raises(RuntimeError, match="holds the advisory lock"):
                command.upgrade(config, "head")
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            lock_connection.commit()
    finally:
        engine.dispose()
