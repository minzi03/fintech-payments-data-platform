"""Structured Portal API logging with bounded redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from portal_api.core.config import PortalApiSettings

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|secret|authorization|cookie|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)(https?://[^:/\s]+:)[^@\s]+@"),
)
_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def redact_text(value: object) -> str:
    """Remove common credentials from diagnostic text."""
    rendered = str(value)
    for pattern in _SECRET_PATTERNS:
        rendered = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", rendered)
    return rendered


class JsonFormatter(logging.Formatter):
    """Emit stable JSON events without arbitrary record internals."""

    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_") or key in payload:
                continue
            if key in {"authorization", "cookie", "password", "secret", "token", "body"}:
                continue
            payload[key] = redact_text(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, separators=(",", ":"), default=str)


class ConsoleFormatter(logging.Formatter):
    """Readable local formatter preserving the same safe fields."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        correlation = getattr(record, "correlation_id", "-")
        request = getattr(record, "request_id", "-")
        message = redact_text(record.getMessage())
        return (
            f"{datetime.now(UTC).isoformat()} {record.levelname} "
            f"event={event} correlation_id={correlation} request_id={request} {message}"
        )


def configure_logging(settings: PortalApiSettings) -> None:
    """Configure root logging once per application creation."""
    level = getattr(logging, settings.log_level.upper(), None)
    if not isinstance(level, int):
        raise ValueError("PORTAL_API_LOG_LEVEL is invalid")
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(settings.service_name, settings.environment.value)
        if settings.log_format == "json"
        else ConsoleFormatter()
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
