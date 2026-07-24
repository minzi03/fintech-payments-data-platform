"""Request and end-to-end correlation context."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

CORRELATION_HEADER = "X-Correlation-ID"
REQUEST_HEADER = "X-Request-ID"
_VALID_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_correlation_id: ContextVar[str] = ContextVar("portal_correlation_id", default="")
_request_id: ContextVar[str] = ContextVar("portal_request_id", default="")


def valid_correlation_id(value: str | None) -> bool:
    """Return whether an external correlation value is bounded and safe."""
    return bool(value and _VALID_CORRELATION_ID.fullmatch(value))


def new_identifier() -> str:
    return str(uuid4())


def set_request_context(inbound_correlation_id: str | None) -> tuple[Token[str], Token[str]]:
    """Set safe correlation and request IDs for the current async context."""
    correlation_id = new_identifier()
    if inbound_correlation_id is not None and valid_correlation_id(inbound_correlation_id):
        correlation_id = inbound_correlation_id
    return _correlation_id.set(correlation_id), _request_id.set(new_identifier())


def reset_request_context(tokens: tuple[Token[str], Token[str]]) -> None:
    _correlation_id.reset(tokens[0])
    _request_id.reset(tokens[1])


def get_correlation_id() -> str:
    """Return the current correlation ID, generating one outside request contexts."""
    current = _correlation_id.get()
    if current:
        return current
    generated = new_identifier()
    _correlation_id.set(generated)
    return generated


def get_request_id() -> str:
    current = _request_id.get()
    if current:
        return current
    generated = new_identifier()
    _request_id.set(generated)
    return generated


def correlation_headers() -> dict[str, str]:
    """Return safe headers for future outbound adapter calls."""
    return {CORRELATION_HEADER: get_correlation_id(), REQUEST_HEADER: get_request_id()}
