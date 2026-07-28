"""Structured logging redaction tests."""

import json
import logging

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    TraceState,
    set_span_in_context,
)
from portal_api.core.logging import JsonFormatter, redact_text


def test_secret_like_values_are_redacted() -> None:
    rendered = redact_text(
        "password=super-secret authorization:Bearer-abc code_verifier=pkce-private "
        "nonce=nonce-private state=state-private "
        "url=https://user:credential@private.example"
    )

    assert "super-secret" not in rendered
    assert "Bearer-abc" not in rendered
    assert "credential@" not in rendered
    assert "pkce-private" not in rendered
    assert "nonce-private" not in rendered
    assert "state-private" not in rendered


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


def test_json_formatter_correlates_trace_and_span_without_log_labels() -> None:
    span_context = SpanContext(
        trace_id=int("1" * 32, 16),
        span_id=int("2" * 16, 16),
        is_remote=False,
        trace_flags=TraceFlags.SAMPLED,
        trace_state=TraceState(),
    )
    token = otel_context.attach(set_span_in_context(NonRecordingSpan(span_context)))
    try:
        record = logging.LogRecord(
            name="portal.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=50,
            msg="correlated event",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonFormatter("portal-api", "test").format(record))
    finally:
        otel_context.detach(token)

    assert payload["trace_id"] == "1" * 32
    assert payload["span_id"] == "2" * 16
