"""Process-local keys for local/test login correlation and protected lookup."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from enum import StrEnum


class ProtectedPurpose(StrEnum):
    LOGIN_INTENT = "login-intent"
    OIDC_STATE = "oidc-state"
    OIDC_NONCE = "oidc-nonce"
    BROWSER_BINDING = "browser-binding"
    SESSION = "session"


@dataclass(frozen=True)
class EphemeralSecurityMaterial:
    """Independent random keys used only by one local/test process lifetime."""

    intent_lookup_key: bytes
    state_lookup_key: bytes
    nonce_lookup_key: bytes
    browser_binding_key: bytes
    session_lookup_key: bytes
    key_version: str = "ephemeral-local-v1"

    @classmethod
    def generate(cls) -> EphemeralSecurityMaterial:
        return cls(
            intent_lookup_key=os.urandom(32),
            state_lookup_key=os.urandom(32),
            nonce_lookup_key=os.urandom(32),
            browser_binding_key=os.urandom(32),
            session_lookup_key=os.urandom(32),
        )

    def protect(self, value: str, *, purpose: ProtectedPurpose) -> bytes:
        keys = {
            ProtectedPurpose.LOGIN_INTENT: self.intent_lookup_key,
            ProtectedPurpose.OIDC_STATE: self.state_lookup_key,
            ProtectedPurpose.OIDC_NONCE: self.nonce_lookup_key,
            ProtectedPurpose.BROWSER_BINDING: self.browser_binding_key,
            ProtectedPurpose.SESSION: self.session_lookup_key,
        }
        domain_separated = purpose.value.encode("ascii") + b"\x00" + value.encode("ascii")
        return hmac.new(keys[purpose], domain_separated, hashlib.sha256).digest()
