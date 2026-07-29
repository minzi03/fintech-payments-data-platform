"""Envelope protection for short-lived local/test security values."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


@dataclass(frozen=True)
class ProtectedValue:
    ciphertext: str
    nonce: str
    wrapped_data_key: str
    wrapped_data_key_nonce: str
    key_reference: str

    def serialize(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def deserialize(cls, value: str) -> ProtectedValue:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("Protected value envelope must be an object")
        return cls(**payload)


class ProtectedValueCipher(Protocol):
    def encrypt(self, plaintext: bytes, *, context: bytes) -> ProtectedValue: ...

    def decrypt(self, protected: ProtectedValue, *, context: bytes) -> bytes: ...


class AesGcmEnvelopeCipher:
    """Per-record AES-GCM envelope backed by a versioned wrapping key."""

    def __init__(
        self,
        wrapping_key: bytes,
        *,
        key_reference: str,
        previous_wrapping_key: bytes | None = None,
        previous_key_reference: str | None = None,
        transition_started_at: datetime | None = None,
        transition_expires_at: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._wrapping_key = wrapping_key
        if len(self._wrapping_key) != 32:
            raise ValueError("Envelope wrapping key must contain 256 bits")
        if not key_reference:
            raise ValueError("Envelope key reference must not be empty")
        self._key_reference = key_reference
        self._previous_wrapping_key = previous_wrapping_key
        self._previous_key_reference = previous_key_reference
        self._transition_started_at = transition_started_at
        self._transition_expires_at = transition_expires_at
        self._clock = clock or _utc_now
        transition_values = (
            previous_wrapping_key,
            previous_key_reference,
            transition_started_at,
            transition_expires_at,
        )
        if any(value is not None for value in transition_values) and not all(
            value is not None for value in transition_values
        ):
            raise ValueError("Previous envelope key requires a complete transition window")
        if previous_wrapping_key is not None:
            if len(previous_wrapping_key) != 32:
                raise ValueError("Previous envelope wrapping key must contain 256 bits")
            if previous_key_reference == key_reference:
                raise ValueError("Current and previous envelope key references must differ")
            if (
                transition_started_at is None
                or transition_expires_at is None
                or transition_expires_at <= transition_started_at
            ):
                raise ValueError("Envelope key transition window is invalid")

    def encrypt(self, plaintext: bytes, *, context: bytes) -> ProtectedValue:
        if not context:
            raise ValueError("Protected value context must not be empty")
        data_key = os.urandom(32)
        value_nonce = os.urandom(12)
        wrapped_nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(value_nonce, plaintext, context)
        wrapped_data_key = AESGCM(self._wrapping_key).encrypt(wrapped_nonce, data_key, context)
        return ProtectedValue(
            ciphertext=_encode(ciphertext),
            nonce=_encode(value_nonce),
            wrapped_data_key=_encode(wrapped_data_key),
            wrapped_data_key_nonce=_encode(wrapped_nonce),
            key_reference=self._key_reference,
        )

    def decrypt(self, protected: ProtectedValue, *, context: bytes) -> bytes:
        wrapping_key = self._wrapping_key_for(protected.key_reference)
        data_key = AESGCM(wrapping_key).decrypt(
            _decode(protected.wrapped_data_key_nonce),
            _decode(protected.wrapped_data_key),
            context,
        )
        return AESGCM(data_key).decrypt(
            _decode(protected.nonce),
            _decode(protected.ciphertext),
            context,
        )

    def _wrapping_key_for(self, key_reference: str) -> bytes:
        if key_reference == self._key_reference:
            return self._wrapping_key
        now = self._clock()
        if (
            self._previous_wrapping_key is not None
            and key_reference == self._previous_key_reference
            and self._transition_started_at is not None
            and self._transition_expires_at is not None
            and self._transition_started_at <= now < self._transition_expires_at
        ):
            return self._previous_wrapping_key
        raise ValueError("Protected value key provider is unavailable")


class EphemeralEnvelopeCipher(AesGcmEnvelopeCipher):
    """Test-only envelope cipher whose wrapping key lasts for one process."""

    def __init__(self, wrapping_key: bytes | None = None) -> None:
        super().__init__(
            wrapping_key or os.urandom(32),
            key_reference="ephemeral-local-v1",
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)
