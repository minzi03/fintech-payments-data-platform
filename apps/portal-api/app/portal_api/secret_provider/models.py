"""Non-secret identities and redacted values for runtime secret resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretBytes, SecretStr

SAFE_IDENTITY = re.compile(r"[a-z][a-z0-9./_-]{0,127}")
SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class SecretValueKind(StrEnum):
    TEXT = "text"
    BYTES = "bytes"


class SecretPurpose(StrEnum):
    DATABASE_CONNECTION = "database-connection"
    AUDIT_DATABASE_CONNECTION = "audit-database-connection"
    OIDC_CLIENT_AUTHENTICATION = "oidc-client-authentication"
    ABUSE_CLIENT_FINGERPRINT = "abuse-client-fingerprint"
    ABUSE_BACKEND_CONNECTION = "abuse-backend-connection"
    SECURITY_MASTER_KEY = "security-master-key"


@dataclass(frozen=True)
class SecretReference:
    """Stable, non-secret identity for one exact secret version."""

    identity: str
    version: str
    purpose: SecretPurpose
    value_kind: SecretValueKind
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if SAFE_IDENTITY.fullmatch(self.identity) is None:
            raise ValueError("Secret reference identity must be a bounded safe identifier")
        if SAFE_VERSION.fullmatch(self.version) is None:
            raise ValueError("Secret reference version must be a bounded safe identifier")
        if self.value_kind is SecretValueKind.BYTES:
            if self.size_bytes is None or self.size_bytes <= 0:
                raise ValueError("Binary secret references require an expected byte size")
        elif self.size_bytes is not None:
            raise ValueError("Text secret references cannot declare a byte size")


@dataclass(frozen=True)
class SecretMetadata:
    """Safe resolution evidence that never contains secret material."""

    provider_id: str
    identity: str
    version: str
    purpose: SecretPurpose
    value_kind: SecretValueKind


@dataclass(frozen=True, repr=False)
class ResolvedSecret:
    """A resolved secret whose representation and metadata are always redacted."""

    reference: SecretReference
    metadata: SecretMetadata
    _value: SecretStr | SecretBytes

    def __post_init__(self) -> None:
        expected = (
            self.reference.identity,
            self.reference.version,
            self.reference.purpose,
            self.reference.value_kind,
        )
        actual = (
            self.metadata.identity,
            self.metadata.version,
            self.metadata.purpose,
            self.metadata.value_kind,
        )
        if actual != expected:
            raise ValueError("Resolved secret metadata does not match its requested reference")
        if self.reference.value_kind is SecretValueKind.TEXT and not isinstance(
            self._value, SecretStr
        ):
            raise TypeError("Text secret reference resolved to an incompatible value kind")
        if self.reference.value_kind is SecretValueKind.BYTES and not isinstance(
            self._value, SecretBytes
        ):
            raise TypeError("Binary secret reference resolved to an incompatible value kind")

    def reveal_text(self) -> str:
        if not isinstance(self._value, SecretStr):
            raise TypeError("Secret reference does not contain text")
        return self._value.get_secret_value()

    def reveal_bytes(self) -> bytes:
        if not isinstance(self._value, SecretBytes):
            raise TypeError("Secret reference does not contain bytes")
        return self._value.get_secret_value()

    def __repr__(self) -> str:
        return (
            "ResolvedSecret("
            f"identity={self.reference.identity!r}, "
            f"version={self.reference.version!r}, "
            f"purpose={self.reference.purpose.value!r}, "
            "value=**********)"
        )
