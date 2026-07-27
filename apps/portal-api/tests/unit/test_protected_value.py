"""Envelope-protection tests for local/test security values."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag
from portal_api.auth.protected_value import EphemeralEnvelopeCipher, ProtectedValue


def test_ephemeral_envelope_round_trip_uses_per_record_wrapped_keys() -> None:
    cipher = EphemeralEnvelopeCipher(bytes(range(32)))
    first = cipher.encrypt(b"first-verifier", context=b"transaction-1")
    second = cipher.encrypt(b"second-verifier", context=b"transaction-2")

    assert cipher.decrypt(first, context=b"transaction-1") == b"first-verifier"
    assert cipher.decrypt(second, context=b"transaction-2") == b"second-verifier"
    assert first.wrapped_data_key != second.wrapped_data_key
    assert first.ciphertext != second.ciphertext
    assert b"first-verifier" not in first.serialize().encode()


def test_envelope_serialization_preserves_protected_representation() -> None:
    cipher = EphemeralEnvelopeCipher(bytes(range(32)))
    protected = cipher.encrypt(b"verifier", context=b"transaction-1")

    restored = ProtectedValue.deserialize(protected.serialize())

    assert restored == protected
    assert cipher.decrypt(restored, context=b"transaction-1") == b"verifier"


def test_wrong_context_or_process_key_fails_closed() -> None:
    cipher = EphemeralEnvelopeCipher(bytes(range(32)))
    protected = cipher.encrypt(b"verifier", context=b"transaction-1")

    with pytest.raises(InvalidTag):
        cipher.decrypt(protected, context=b"transaction-2")
    with pytest.raises(InvalidTag):
        EphemeralEnvelopeCipher(bytes(reversed(range(32)))).decrypt(
            protected,
            context=b"transaction-1",
        )


def test_empty_context_and_invalid_key_are_rejected() -> None:
    with pytest.raises(ValueError, match="256 bits"):
        EphemeralEnvelopeCipher(b"short")
    with pytest.raises(ValueError, match="must not be empty"):
        EphemeralEnvelopeCipher(bytes(range(32))).encrypt(b"value", context=b"")
