"""Structured logging redaction tests."""

import json
import logging

from portal_api.core.logging import JsonFormatter, redact_text


def test_secret_like_values_are_redacted() -> None:
    rendered = redact_text(
        "password=super-secret authorization:Bearer-abc url=https://user:credential@private.example"
    )

    assert "super-secret" not in rendered
    assert "Bearer-abc" not in rendered
    assert "credential@" not in rendered


def test_json_formatter_contains_safe_context_without_exception_message_secret() -> None:
    record = logging.LogRecord(
        name="portal.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="dependency failed password=never-log-this",
        args=(),
        exc_info=None,
    )
    record.event = "dependency_failed"
    record.correlation_id = "correlation-1"
    payload = json.loads(JsonFormatter("portal-api", "test").format(record))

    assert payload["event"] == "dependency_failed"
    assert payload["correlation_id"] == "correlation-1"
    assert "never-log-this" not in json.dumps(payload)
