"""Audit outbox domain, retry, privacy, and configuration tests."""

from __future__ import annotations

from random import Random
from uuid import uuid4

import pytest
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    AuditDestination,
    RetryPolicy,
)
from portal_api.audit.redaction import UnsafeAuditPayload
from portal_api.core.config import PortalApiSettings
from pydantic import ValidationError


def _message(payload: dict[str, object] | None = None) -> AuditDeliveryMessage:
    event_id = uuid4()
    return AuditDeliveryMessage(
        outbox_id=event_id,
        audit_event_id=event_id,
        event_type="auth.login_started.v1",
        event_family="auth",
        destination=AuditDestination.LOCAL_POSTGRES,
        payload_version=1,
        payload=payload or {"schema_version": 1, "outcome": "STARTED"},
        attempt_count=1,
        max_attempts=5,
        lease_token=uuid4(),
    )


def test_idempotency_and_serialization_are_stable() -> None:
    left = _message({"b": 2, "a": 1})
    right = AuditDeliveryMessage(
        **{
            **left.__dict__,
            "payload": {"a": 1, "b": 2},
        }
    )

    assert left.idempotency_key == right.idempotency_key
    assert left.payload_bytes == right.payload_bytes == b'{"a":1,"b":2}'


def test_payload_rejects_sensitive_and_unbounded_values() -> None:
    with pytest.raises(UnsafeAuditPayload, match="Forbidden audit field"):
        _ = _message({"refresh_token": "forbidden"}).payload_bytes
    with pytest.raises(ValueError, match="bounded delivery size"):
        _ = _message({"safe": "x" * (65 * 1024)}).payload_bytes


def test_retry_policy_is_bounded_deterministic_and_never_negative() -> None:
    policy = RetryPolicy(1, 8, jitter_ratio=0.2)

    assert policy.delay(1, random_source=Random(1)).total_seconds() >= 0
    assert policy.delay(4, random_source=Random(2)).total_seconds() <= 8
    assert policy.delay(20, random_source=Random(3)).total_seconds() <= 8
    with pytest.raises(ValueError, match="positive"):
        policy.delay(0)


def test_outbox_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="lease must exceed"):
        PortalApiSettings(
            _env_file=None,
            audit_outbox_lease_seconds=5,
            audit_outbox_delivery_timeout_seconds=5,
        )
    with pytest.raises(ValidationError, match="AUDIT_WORKER_DATABASE_URL"):
        PortalApiSettings(_env_file=None, audit_outbox_enabled=True)
    with pytest.raises(ValidationError, match="local_postgres"):
        PortalApiSettings(_env_file=None, audit_outbox_destination="unbounded-url")


def test_valid_worker_configuration_is_accepted() -> None:
    settings = PortalApiSettings(
        _env_file=None,
        audit_outbox_enabled=True,
        audit_worker_database_url=(
            "postgresql+psycopg://portal_archive:example@localhost/portal_control"
        ),
    )

    assert settings.audit_outbox_enabled is True
    assert settings.audit_outbox_batch_size == 100
