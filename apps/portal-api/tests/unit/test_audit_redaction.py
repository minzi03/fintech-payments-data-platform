"""Fail-closed audit payload validation tests."""

from __future__ import annotations

import pytest
from portal_api.audit.redaction import UnsafeAuditPayload, assert_audit_safe


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "private"},
        {"nested": {"codeVerifier": "private"}},
        {"items": [{"browser-binding": "private"}]},
        {"payload": b"binary-private"},
        {"detail": "Authorization: Bearer abc.def"},
        {"detail": "eyJhbGciOiJIUzI1NiJ9.cGF5bG9hZA.c2lnbmF0dXJl"},
    ],
)
def test_forbidden_audit_payloads_are_rejected(payload: object) -> None:
    with pytest.raises(UnsafeAuditPayload):
        assert_audit_safe(payload)


def test_safe_classifications_and_references_are_allowed() -> None:
    assert_audit_safe(
        {
            "transaction_reference": "tx-opaque-reference",
            "failure_phase": "pre_dispatch",
            "classifications": ["loopback", "known-user-agent"],
            "attempt": 1,
        }
    )
