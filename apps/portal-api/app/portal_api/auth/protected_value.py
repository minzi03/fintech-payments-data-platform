"""Envelope protection for short-lived local/test security values."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass
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


class EphemeralEnvelopeCipher:
    """Per-record AES-GCM envelope backed by a process-local random wrapping key.

    This provider is deliberately limited to local and test execution. Process restart makes
    earlier envelopes unreadable and therefore fail closed.
    """

    def __init__(self, wrapping_key: bytes | None = None) -> None:
        self._wrapping_key = wrapping_key or os.urandom(32)
        if len(self._wrapping_key) != 32:
            raise ValueError("Ephemeral wrapping key must contain 256 bits")

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
            key_reference="ephemeral-local-v1",
        )

    def decrypt(self, protected: ProtectedValue, *, context: bytes) -> bytes:
        if protected.key_reference != "ephemeral-local-v1":
            raise ValueError("Protected value key provider is unavailable")
        data_key = AESGCM(self._wrapping_key).decrypt(
            _decode(protected.wrapped_data_key_nonce),
            _decode(protected.wrapped_data_key),
            context,
        )
        return AESGCM(data_key).decrypt(
            _decode(protected.nonce),
            _decode(protected.ciphertext),
            context,
        )
