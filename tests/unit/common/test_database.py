"""Unit tests for safe PostgreSQL connection cleanup."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from common.config import DatabaseSettings
from common.database import database_connection


class FakeConnection:
    """Minimal psycopg connection double for lifecycle assertions."""

    def __init__(self, rollback_error: Exception | None = None) -> None:
        self.closed = False
        self.rollback_calls = 0
        self.rollback_error = rollback_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error:
            raise self.rollback_error

    def close(self) -> None:
        self.closed = True


def _settings(database_url: str | None = None) -> DatabaseSettings:
    return DatabaseSettings(
        host="localhost",
        port=5432,
        database="fintech_payments",
        user="payments_app",
        password="test-placeholder",
        database_url=database_url,
    )


def test_database_url_connection_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    captured: dict[str, Any] = {}

    def fake_connect(*args: object, **kwargs: object) -> FakeConnection:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return connection

    monkeypatch.setattr("common.database.psycopg.connect", fake_connect)

    with database_connection(_settings("postgresql://user:value@localhost/db")) as opened:
        assert opened is connection

    assert captured["args"] == ("postgresql://user:value@localhost/db",)
    assert captured["kwargs"]["autocommit"] is False
    assert connection.closed


def test_individual_settings_are_passed_and_exception_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: object) -> FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr("common.database.psycopg.connect", fake_connect)

    with (
        pytest.raises(RuntimeError, match="iteration failed"),
        database_connection(_settings()),
    ):
        raise RuntimeError("iteration failed")

    assert captured["dbname"] == "fintech_payments"
    assert captured["password"] == "test-placeholder"
    assert connection.rollback_calls == 1
    assert connection.closed


def test_cleanup_closes_connection_when_rollback_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback_error = psycopg.Error("rollback failed")
    connection = FakeConnection(rollback_error=rollback_error)
    monkeypatch.setattr("common.database.psycopg.connect", lambda **_kwargs: connection)

    with pytest.raises(ValueError, match="original"), database_connection(_settings()):
        raise ValueError("original")

    assert connection.rollback_calls == 1
    assert connection.closed
