"""Domain-separated keys for login correlation and protected lookup."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


class ProtectedPurpose(StrEnum):
    LOGIN_INTENT = "login-intent"
    OIDC_STATE = "oidc-state"
    OIDC_NONCE = "oidc-nonce"
    BROWSER_BINDING = "browser-binding"
    SESSION = "session"
    CSRF_DERIVE = "csrf-derive"
    CSRF_LOOKUP = "csrf-lookup"
    PROVIDER_REFRESH_TOKEN = "provider-refresh-token"
    PROVIDER_LOGOUT_JTI = "provider-logout-jti"


@dataclass(frozen=True)
class EphemeralSecurityMaterial:
    """Runtime keys that may be random for tests or derived for local/development."""

    intent_lookup_key: bytes
    state_lookup_key: bytes
    nonce_lookup_key: bytes
    browser_binding_key: bytes
    session_lookup_key: bytes
    csrf_derive_key: bytes
    csrf_lookup_key: bytes
    key_version: str = "ephemeral-local-v1"
    previous_material: EphemeralSecurityMaterial | None = None
    transition_started_at: datetime | None = None
    transition_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        transition_values = (
            self.previous_material,
            self.transition_started_at,
            self.transition_expires_at,
        )
        if any(value is not None for value in transition_values) and not all(
            value is not None for value in transition_values
        ):
            raise ValueError("Previous security material requires a complete transition window")
        if self.previous_material is not None:
            if self.previous_material.key_version == self.key_version:
                raise ValueError("Current and previous security key versions must differ")
            if (
                self.transition_started_at is None
                or self.transition_expires_at is None
                or self.transition_expires_at <= self.transition_started_at
            ):
                raise ValueError("Security key transition window is invalid")

    @classmethod
    def generate(cls) -> EphemeralSecurityMaterial:
        return cls(
            intent_lookup_key=os.urandom(32),
            state_lookup_key=os.urandom(32),
            nonce_lookup_key=os.urandom(32),
            browser_binding_key=os.urandom(32),
            session_lookup_key=os.urandom(32),
            csrf_derive_key=os.urandom(32),
            csrf_lookup_key=os.urandom(32),
        )

    @classmethod
    def from_master_key(
        cls,
        master_key: bytes,
        *,
        key_version: str,
    ) -> EphemeralSecurityMaterial:
        """Derive restart-stable, purpose-separated runtime keys."""
        if len(master_key) != 32:
            raise ValueError("Security master key must contain 256 bits")
        return cls(
            intent_lookup_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="login-intent-lookup",
            ),
            state_lookup_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="oidc-state-lookup",
            ),
            nonce_lookup_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="oidc-nonce-lookup",
            ),
            browser_binding_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="browser-binding-lookup",
            ),
            session_lookup_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="session-lookup",
            ),
            csrf_derive_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="csrf-derive",
            ),
            csrf_lookup_key=derive_security_key(
                master_key,
                key_version=key_version,
                purpose="csrf-lookup",
            ),
            key_version=key_version,
        )

    def protect(self, value: str, *, purpose: ProtectedPurpose) -> bytes:
        keys = {
            ProtectedPurpose.LOGIN_INTENT: self.intent_lookup_key,
            ProtectedPurpose.OIDC_STATE: self.state_lookup_key,
            ProtectedPurpose.OIDC_NONCE: self.nonce_lookup_key,
            ProtectedPurpose.BROWSER_BINDING: self.browser_binding_key,
            ProtectedPurpose.SESSION: self.session_lookup_key,
            ProtectedPurpose.CSRF_DERIVE: self.csrf_derive_key,
            ProtectedPurpose.CSRF_LOOKUP: self.csrf_lookup_key,
            ProtectedPurpose.PROVIDER_REFRESH_TOKEN: self.session_lookup_key,
            ProtectedPurpose.PROVIDER_LOGOUT_JTI: self.state_lookup_key,
        }
        domain_separated = purpose.value.encode("ascii") + b"\x00" + value.encode("ascii")
        return hmac.new(keys[purpose], domain_separated, hashlib.sha256).digest()

    def with_previous(
        self,
        previous: EphemeralSecurityMaterial,
        *,
        transition_started_at: datetime,
        transition_expires_at: datetime,
    ) -> EphemeralSecurityMaterial:
        """Attach one bounded previous-key transition to the selected current key."""
        return replace(
            self,
            previous_material=previous,
            transition_started_at=transition_started_at,
            transition_expires_at=transition_expires_at,
        )

    def candidate_materials(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[EphemeralSecurityMaterial, ...]:
        """Return current plus an active previous material in deterministic order."""
        selected = [self]
        if self.previous_material is not None and self._transition_is_active(at=at):
            selected.append(self.previous_material)
        return tuple(selected)

    def protect_candidates(
        self,
        value: str,
        *,
        purpose: ProtectedPurpose,
        at: datetime | None = None,
    ) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (
                material.key_version,
                material.protect(value, purpose=purpose),
            )
            for material in self.candidate_materials(at=at)
        )

    def material_for_version(
        self,
        key_version: str,
        *,
        at: datetime | None = None,
    ) -> EphemeralSecurityMaterial | None:
        return next(
            (
                material
                for material in self.candidate_materials(at=at)
                if material.key_version == key_version
            ),
            None,
        )

    def _transition_is_active(self, *, at: datetime | None) -> bool:
        if self.transition_started_at is None or self.transition_expires_at is None:
            return False
        evaluated_at = at or datetime.now(UTC)
        return self.transition_started_at <= evaluated_at < self.transition_expires_at


def derive_security_key(
    master_key: bytes,
    *,
    key_version: str,
    purpose: str,
) -> bytes:
    """Derive one stable key without reusing master-key bytes across purposes."""
    if len(master_key) != 32:
        raise ValueError("Security master key must contain 256 bits")
    context = (
        b"fintech-portal-local-security-v1\x00"
        + key_version.encode("ascii")
        + b"\x00"
        + purpose.encode("ascii")
    )
    return hmac.new(master_key, context, hashlib.sha256).digest()
