"""Tests for generator CLI parsing and validation."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from generators.cli import main, parse_generator_config


def test_cli_parses_required_phase_one_controls() -> None:
    config = parse_generator_config(
        [
            "--once",
            "--seed",
            "91",
            "--customers",
            "4",
            "--merchants",
            "3",
            "--transactions",
            "12",
            "--invalid-rate",
            "0.1",
            "--duplicate-rate",
            "0.2",
        ],
        {},
    )

    assert config.seed == 91
    assert config.customers == 4
    assert config.merchants == 3
    assert config.transactions == 12
    assert config.invalid_rate == 0.1
    assert config.duplicate_rate == 0.2


def test_cli_reads_generation_defaults_from_environment() -> None:
    config = parse_generator_config(
        ["--once"],
        {
            "GENERATOR_SEED": "7",
            "GENERATOR_CUSTOMERS": "6",
            "GENERATOR_MERCHANTS": "2",
            "GENERATOR_TRANSACTIONS": "9",
            "GENERATOR_INVALID_RATE": "0.25",
            "GENERATOR_DUPLICATE_RATE": "0.5",
        },
    )

    assert config.seed == 7
    assert config.customers == 6
    assert config.transactions == 9
    assert config.invalid_rate == 0.25
    assert config.duplicate_rate == 0.5


def test_cli_requires_explicit_once_mode() -> None:
    with pytest.raises(SystemExit):
        parse_generator_config([], {})


@pytest.mark.parametrize("rate", ["-0.01", "1.01", "nan"])
def test_cli_rejects_invalid_rate_boundaries(rate: str) -> None:
    with pytest.raises(SystemExit):
        parse_generator_config(["--once", "--invalid-rate", rate], {})


@pytest.mark.parametrize("rate", ["-0.01", "1.01", "inf"])
def test_cli_rejects_duplicate_rate_boundaries(rate: str) -> None:
    with pytest.raises(SystemExit):
        parse_generator_config(["--once", "--duplicate-rate", rate], {})


def test_main_runs_one_transactional_iteration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "fintech_payments",
        "POSTGRES_USER": "payments_app",
        "POSTGRES_PASSWORD": "test-placeholder",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    connection = SimpleNamespace(transaction=lambda: nullcontext())

    @contextmanager
    def fake_database_connection(_settings: object):
        yield connection

    summary = SimpleNamespace(
        customers=2,
        accounts=2,
        merchants=1,
        transactions=3,
        events=5,
        refunds=1,
        invalid_rejections=0,
        duplicate_rejections=0,
    )

    class FakeRepository:
        def __init__(self, opened_connection: Any) -> None:
            assert opened_connection is connection

        def persist(self, dataset: object) -> object:
            assert dataset is not None
            return summary

    monkeypatch.setattr("generators.cli.database_connection", fake_database_connection)
    monkeypatch.setattr("generators.cli.PaymentRepository", FakeRepository)

    result = main(
        [
            "--once",
            "--seed",
            "11",
            "--customers",
            "2",
            "--merchants",
            "1",
            "--transactions",
            "3",
        ]
    )

    assert result == 0


def test_main_returns_configuration_error_without_database_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("generators.cli.configure_logging", lambda _level: None)

    assert main(["--once"]) == 2
