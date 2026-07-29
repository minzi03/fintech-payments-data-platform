"""Environment-backed implementation of the vendor-neutral provider contract."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import SecretBytes, SecretStr

from portal_api.secret_provider.models import (
    ResolvedSecret,
    SecretMetadata,
    SecretReference,
    SecretValueKind,
)
from portal_api.secret_provider.provider import (
    SecretProviderError,
    SecretProviderFailure,
    SecretProviderStatus,
)


@dataclass(frozen=True)
class EnvironmentSecretEntry:
    """One already-redacted Pydantic environment setting and its safe version."""

    version: str
    value: SecretStr | SecretBytes


class EnvironmentSecretProvider:
    """Resolve existing Portal environment settings through the provider boundary."""

    def __init__(
        self,
        entries: Mapping[tuple[str, str], EnvironmentSecretEntry],
        *,
        provider_id: str = "environment",
    ) -> None:
        self._provider_id = provider_id
        self._entries = MappingProxyType(dict(entries))
        self._started = False

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def start(self) -> None:
        self._started = True

    def status(self) -> SecretProviderStatus:
        return SecretProviderStatus(
            provider_id=self.provider_id,
            available=self._started,
            status_code="available" if self._started else "not-started",
        )

    def resolve(self, reference: SecretReference) -> ResolvedSecret:
        if not self._started:
            raise SecretProviderError(
                SecretProviderFailure.NOT_STARTED,
                provider_id=self.provider_id,
                reference=reference,
            )
        entry = self._entries.get((reference.identity, reference.version))
        if entry is None:
            if any(identity == reference.identity for identity, _ in self._entries):
                raise SecretProviderError(
                    SecretProviderFailure.VERSION_MISMATCH,
                    provider_id=self.provider_id,
                    reference=reference,
                )
            raise SecretProviderError(
                SecretProviderFailure.NOT_FOUND,
                provider_id=self.provider_id,
                reference=reference,
            )
        if entry.version != reference.version:
            raise SecretProviderError(
                SecretProviderFailure.VERSION_MISMATCH,
                provider_id=self.provider_id,
                reference=reference,
            )
        try:
            value = self._coerce_value(reference, entry.value)
        except (UnicodeError, binascii.Error, ValueError) as error:
            raise SecretProviderError(
                SecretProviderFailure.INVALID_VALUE,
                provider_id=self.provider_id,
                reference=reference,
            ) from error
        return ResolvedSecret(
            reference=reference,
            metadata=SecretMetadata(
                provider_id=self.provider_id,
                identity=reference.identity,
                version=reference.version,
                purpose=reference.purpose,
                value_kind=reference.value_kind,
            ),
            _value=value,
        )

    def close(self) -> None:
        self._started = False

    @staticmethod
    def _coerce_value(
        reference: SecretReference,
        value: SecretStr | SecretBytes,
    ) -> SecretStr | SecretBytes:
        if reference.value_kind is SecretValueKind.TEXT:
            if isinstance(value, SecretBytes):
                return SecretStr(value.get_secret_value().decode("utf-8"))
            return value

        if isinstance(value, SecretBytes):
            decoded = value.get_secret_value()
        else:
            encoded = value.get_secret_value().encode("ascii")
            decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(decoded) != reference.size_bytes:
            raise ValueError("Binary secret has an invalid size")
        return SecretBytes(decoded)
