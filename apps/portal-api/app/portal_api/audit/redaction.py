"""Fail-closed validation for append-only audit payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "access_token",
        "authorization",
        "authorization_code",
        "body",
        "browser_binding",
        "client_secret",
        "code_verifier",
        "cookie",
        "email",
        "financial",
        "id_token",
        "nonce",
        "password",
        "pkce",
        "raw_claim",
        "refresh_token",
        "request_body",
        "response_body",
        "secret",
        "session_secret",
        "state_value",
        "token_response",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


class UnsafeAuditPayload(ValueError):
    """Raised before an unsafe value can enter the security ledger."""


def _normalized_key(value: object) -> str:
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    return re.sub(r"[^a-z0-9]+", "_", with_boundaries.lower()).strip("_")


def assert_audit_safe(value: Any, *, path: str = "safe_metadata") -> None:
    """Recursively reject forbidden keys, binary values, and token-shaped strings."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise UnsafeAuditPayload(f"Forbidden audit field at {path}.{normalized}")
            assert_audit_safe(item, path=f"{path}.{normalized}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            assert_audit_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(value, (bytes, bytearray)):
        raise UnsafeAuditPayload(f"Binary audit value is forbidden at {path}")
    if isinstance(value, str) and (
        _BEARER_PATTERN.search(value) is not None or _JWT_PATTERN.search(value) is not None
    ):
        raise UnsafeAuditPayload(f"Token-shaped audit value is forbidden at {path}")
