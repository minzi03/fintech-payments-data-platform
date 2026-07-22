"""Tests for bounded, idempotent, and redacted Kafka Connect operations."""

from collections.abc import Mapping
from typing import Any
from urllib.error import URLError

import pytest

from ingestion.cdc.config import CdcSettings, ConnectorDefinition
from ingestion.cdc.connect_api import (
    ConnectApiError,
    ConnectClient,
    EnsureAction,
    HttpResponse,
    connector_configs_equal,
    summarize_status,
)


def settings() -> CdcSettings:
    return CdcSettings(
        connect_url="http://localhost:8083",
        connector_name="payments-postgres-cdc",
        database_host="postgres",
        database_port=5432,
        database_name="fintech_payments",
        database_user="payments_cdc",
        database_password="never-print-this",
        http_max_attempts=3,
    )


def definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        name="payments-postgres-cdc",
        config={
            "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
            "database.password": "never-print-this",
            "topic.prefix": "fintech.cdc",
        },
    )


class FakeTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        assert timeout_seconds == 10
        self.calls.append((method, url, payload))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_missing_connector_is_created_with_one_json_object_payload() -> None:
    transport = FakeTransport(
        [
            HttpResponse(200, {"error_count": 0}),
            HttpResponse(404, {"message": "not found"}),
            HttpResponse(201, {"name": "payments-postgres-cdc"}),
        ]
    )
    client = ConnectClient(settings(), transport=transport, sleeper=lambda _: None)

    assert client.ensure(definition()) is EnsureAction.CREATED
    assert transport.calls[0][2] == {"name": definition().name, **definition().config}
    assert transport.calls[-1][0] == "POST"
    assert transport.calls[-1][2] == {
        "name": "payments-postgres-cdc",
        "config": definition().config,
    }


def test_equivalent_masked_connector_is_unchanged() -> None:
    existing = dict(definition().config)
    existing["database.password"] = "********"
    transport = FakeTransport([HttpResponse(200, {"error_count": 0}), HttpResponse(200, existing)])

    action = ConnectClient(settings(), transport=transport).ensure(definition())

    assert action is EnsureAction.UNCHANGED
    assert [call[0] for call in transport.calls] == ["PUT", "GET"]


def test_changed_connector_is_updated() -> None:
    existing = dict(definition().config)
    existing["topic.prefix"] = "old.prefix"
    transport = FakeTransport(
        [
            HttpResponse(200, {"error_count": 0}),
            HttpResponse(200, existing),
            HttpResponse(200, definition().config),
        ]
    )

    assert (
        ConnectClient(settings(), transport=transport).ensure(definition()) is EnsureAction.UPDATED
    )
    assert transport.calls[-1][0] == "PUT"


def test_retry_exhaustion_maps_transport_error_without_password() -> None:
    transport = FakeTransport([URLError("down"), URLError("down"), URLError("down")])
    client = ConnectClient(settings(), transport=transport, sleeper=lambda _: None)

    with pytest.raises(ConnectApiError, match="after 3 attempts") as error:
        client.wait_ready()

    assert "never-print-this" not in str(error.value)
    assert len(transport.calls) == 3


def test_validation_error_redacts_connector_password() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                200,
                {
                    "error_count": 1,
                    "configs": [
                        {"value": {"errors": ["bad value never-print-this"]}},
                    ],
                },
            )
        ]
    )

    with pytest.raises(ConnectApiError, match=r"bad value \*\*\*") as error:
        ConnectClient(settings(), transport=transport).validate(definition())

    assert "never-print-this" not in str(error.value)


def test_running_status_requires_connector_and_all_tasks() -> None:
    starting: dict[str, Any] = {
        "name": "payments-postgres-cdc",
        "connector": {"state": "RUNNING", "worker_id": "connect:8083"},
        "tasks": [{"id": 0, "state": "STARTING", "worker_id": "connect:8083"}],
    }
    running: dict[str, Any] = {
        **starting,
        "tasks": [{"id": 0, "state": "RUNNING", "worker_id": "connect:8083"}],
    }
    transport = FakeTransport([HttpResponse(200, starting), HttpResponse(200, running)])

    payload = ConnectClient(settings(), transport=transport, sleeper=lambda _: None).wait_running()

    assert summarize_status(payload)["tasks"] == [
        {"id": 0, "state": "RUNNING", "worker_id": "connect:8083"}
    ]


def test_config_comparison_rejects_extra_or_changed_keys() -> None:
    assert not connector_configs_equal({"a": "1", "extra": "2"}, {"a": "1"})
    assert not connector_configs_equal({"a": "2"}, {"a": "1"})


def test_config_comparison_ignores_connect_internal_name() -> None:
    assert connector_configs_equal(
        {"name": "payments-postgres-cdc", "a": "1"},
        {"a": "1"},
    )
