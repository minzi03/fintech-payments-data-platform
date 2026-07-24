"""Correlation ID validation and context tests."""

from portal_api.core.correlation import (
    get_correlation_id,
    reset_request_context,
    set_request_context,
    valid_correlation_id,
)


def test_valid_inbound_correlation_id_is_preserved() -> None:
    tokens = set_request_context("incident-20260724:attempt.1")
    try:
        assert get_correlation_id() == "incident-20260724:attempt.1"
    finally:
        reset_request_context(tokens)


def test_invalid_or_unbounded_correlation_id_is_replaced() -> None:
    assert not valid_correlation_id("../unsafe")
    assert not valid_correlation_id("x" * 129)

    tokens = set_request_context("../unsafe")
    try:
        assert get_correlation_id() != "../unsafe"
        assert valid_correlation_id(get_correlation_id())
    finally:
        reset_request_context(tokens)
