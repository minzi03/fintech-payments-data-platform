"""Unit tests for centralized logging configuration."""

import logging

import pytest

from common.logging import configure_logging


def test_configure_logging_normalizes_level(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))

    configure_logging("info")

    assert captured["level"] == logging.INFO
    assert captured["force"] is True
    assert "%(name)s" in str(captured["format"])


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="Unsupported log level"):
        configure_logging("verbose-secret")
