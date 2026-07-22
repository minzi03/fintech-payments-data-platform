"""Typed CDC consumer configuration tests."""

from __future__ import annotations

import argparse

import pytest

from common.config import ConfigurationError
from ingestion.cdc_consumer import cli
from ingestion.cdc_consumer.config import CdcConsumerSettings
from ingestion.cdc_consumer.models import ConsumerRunResult

TOPICS = "fintech.cdc.payments.customers,fintech.cdc.payments.payment_transactions"


def environment() -> dict[str, str]:
    return {
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:29092",
        "KAFKA_TOPIC_PREFIX": "fintech.cdc",
        "CDC_CONSUMER_TOPICS": TOPICS,
    }


def test_config_disables_auto_commit_and_auto_offset_store() -> None:
    settings = CdcConsumerSettings.from_env(environment())
    kafka = settings.kafka_config()
    assert kafka["enable.auto.commit"] is False
    assert kafka["enable.auto.offset.store"] is False
    assert settings.group_id == "fintech-cdc-bronze-v1"


@pytest.mark.parametrize("value", ["", "0", "-1"])
def test_batch_size_must_be_positive(value: str) -> None:
    env = environment() | {"CDC_CONSUMER_BATCH_SIZE": value}
    with pytest.raises(ConfigurationError):
        CdcConsumerSettings.from_env(env)


@pytest.mark.parametrize("value", ["0", "-0.1", "invalid"])
def test_flush_interval_must_be_positive(value: str) -> None:
    env = environment() | {"CDC_CONSUMER_FLUSH_INTERVAL_SECONDS": value}
    with pytest.raises(ConfigurationError):
        CdcConsumerSettings.from_env(env)


def test_auto_offset_reset_has_bounded_values() -> None:
    env = environment() | {"CDC_CONSUMER_AUTO_OFFSET_RESET": "none"}
    with pytest.raises(ConfigurationError, match="earliest or latest"):
        CdcConsumerSettings.from_env(env)


def test_topics_are_explicit_allowlist_only() -> None:
    for value in ("fintech.cdc.payments.*", "other.topic"):
        with pytest.raises(ConfigurationError):
            CdcConsumerSettings.from_env(environment() | {"CDC_CONSUMER_TOPICS": value})


def test_secret_bearing_storage_config_is_not_part_of_consumer_repr() -> None:
    settings = CdcConsumerSettings.from_env(environment())
    rendered = repr(settings)
    assert "password" not in rendered.lower()
    assert "secret" not in rendered.lower()


def test_cli_parses_bounded_run_options() -> None:
    args = cli.build_parser(environment()).parse_args(
        [
            "run",
            "--topics",
            "fintech.cdc.payments.customers",
            "--group-id",
            "demo-group",
            "--batch-size",
            "7",
            "--flush-interval",
            "1.5",
            "--max-messages",
            "10",
            "--once",
            "--dry-run",
        ]
    )
    settings = cli._settings_with_cli(CdcConsumerSettings.from_env(environment()), args)
    assert settings.topics == ("fintech.cdc.payments.customers",)
    assert settings.group_id == "demo-group"
    assert settings.batch_size == 7
    assert args.once and args.dry_run


def test_cli_run_wires_dependencies_without_exposing_payload(monkeypatch, capsys, tmp_path) -> None:
    env = environment() | {
        "STORAGE_BACKEND": "local",
        "SETTLEMENT_BRONZE_DIR": str(tmp_path / "bronze"),
        "SETTLEMENT_QUARANTINE_DIR": str(tmp_path / "quarantine"),
        "CDC_CONSUMER_MANIFEST_DB": str(tmp_path / "manifest.sqlite3"),
        "CDC_CONSUMER_TEMP_DIR": str(tmp_path / "temp"),
    }
    args = cli.build_parser(env).parse_args(["run", "--once", "--storage-backend", "local"])

    class FakeConsumer:
        def __init__(self, config):
            assert config["enable.auto.commit"] is False

    class FakeService:
        def __init__(self, **kwargs):
            assert kwargs["dry_run"] is False

        def run(self, **kwargs):
            assert kwargs["once"] is True
            return ConsumerRunResult(1, 1, 0, 0, 1, 1, False)

    monkeypatch.setattr(cli, "Consumer", FakeConsumer)
    monkeypatch.setattr(cli, "ReliableCdcConsumer", FakeService)
    assert cli._run(args, env) == 0
    output = capsys.readouterr().out
    assert '"committed_count": 1' in output
    assert "before_json" not in output


def test_cli_inspect_and_guarded_reset(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    manifest_path = tmp_path / "data/control/cdc_consumer_manifest.sqlite3"
    env = environment() | {"CDC_CONSUMER_MANIFEST_DB": str(manifest_path)}
    inspect_args = argparse.Namespace(object_uri=None, storage_backend="local")
    assert cli._inspect(inspect_args, env) == 0
    assert '"batches": []' in capsys.readouterr().out

    reset_args = argparse.Namespace(confirm=False)
    with pytest.raises(ValueError, match="requires --confirm"):
        cli._reset(reset_args, env)
    reset_args.confirm = True
    assert cli._reset(reset_args, env) == 0
    assert not manifest_path.exists()


def test_cli_main_returns_safe_error_for_missing_configuration(monkeypatch) -> None:
    monkeypatch.setattr(cli.os, "environ", {"LOG_LEVEL": "INFO"})
    assert cli.main(["run", "--dry-run"]) == 2
