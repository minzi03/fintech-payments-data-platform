"""Envelope-protection tests for local/test security values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag
from portal_api.auth.protected_value import (
    AesGcmEnvelopeCipher,
    EphemeralEnvelopeCipher,
    ProtectedValue,
)


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


def test_versioned_envelope_is_restart_stable_and_rejects_unknown_version() -> None:
    first_process = AesGcmEnvelopeCipher(bytes(range(32)), key_reference="local-v1")
    protected = first_process.encrypt(b"refresh-token", context=b"session-family")
    restarted_process = AesGcmEnvelopeCipher(bytes(range(32)), key_reference="local-v1")

    assert restarted_process.decrypt(protected, context=b"session-family") == b"refresh-token"
    with pytest.raises(ValueError, match="provider is unavailable"):
        AesGcmEnvelopeCipher(
            bytes(range(32)),
            key_reference="local-v2",
        ).decrypt(protected, context=b"session-family")


def test_previous_envelope_decrypts_during_transition_and_expires_deterministically() -> None:
    started_at = datetime(2026, 7, 27, 1, tzinfo=UTC)
    expires_at = started_at + timedelta(hours=1)
    observed_at = [started_at]
    previous = AesGcmEnvelopeCipher(bytes(range(32)), key_reference="local-v1")
    protected = previous.encrypt(b"refresh-token", context=b"session-family")

    def clock() -> datetime:
        return observed_at[0]

    restarted_during_transition = AesGcmEnvelopeCipher(
        bytes(range(32, 64)),
        key_reference="local-v2",
        previous_wrapping_key=bytes(range(32)),
        previous_key_reference="local-v1",
        transition_started_at=started_at,
        transition_expires_at=expires_at,
        clock=clock,
    )
    assert (
        restarted_during_transition.decrypt(protected, context=b"session-family")
        == b"refresh-token"
    )

    observed_at[0] = expires_at
    with pytest.raises(ValueError, match="provider is unavailable"):
        restarted_during_transition.decrypt(protected, context=b"session-family")


def test_explicit_key_selection_supports_controlled_rollback_during_transition() -> None:
    started_at = datetime(2026, 7, 27, 1, tzinfo=UTC)
    expires_at = started_at + timedelta(hours=1)
    rotated = AesGcmEnvelopeCipher(bytes(range(32, 64)), key_reference="local-v2")
    protected = rotated.encrypt(b"provider-token", context=b"session-family")
    rollback = AesGcmEnvelopeCipher(
        bytes(range(32)),
        key_reference="local-v1",
        previous_wrapping_key=bytes(range(32, 64)),
        previous_key_reference="local-v2",
        transition_started_at=started_at,
        transition_expires_at=expires_at,
        clock=lambda: started_at + timedelta(minutes=1),
    )

    assert rollback.decrypt(protected, context=b"session-family") == b"provider-token"
