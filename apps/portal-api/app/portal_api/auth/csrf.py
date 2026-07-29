"""Synchronizer-token derivation and protected comparison."""

from __future__ import annotations

import base64
import hmac

from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose


def derive_csrf_token(
    *,
    security_material: EphemeralSecurityMaterial,
    session_secret: str,
    generation: int,
) -> str:
    if generation <= 0:
        raise ValueError("CSRF generation must be positive")
    derived = security_material.protect(
        f"{session_secret}:{generation}",
        purpose=ProtectedPurpose.CSRF_DERIVE,
    )
    return base64.urlsafe_b64encode(derived).rstrip(b"=").decode("ascii")


def csrf_token_hash(
    *,
    security_material: EphemeralSecurityMaterial,
    token: str,
) -> bytes:
    return security_material.protect(token, purpose=ProtectedPurpose.CSRF_LOOKUP)


def csrf_token_matches(
    *,
    security_material: EphemeralSecurityMaterial,
    token: str,
    expected_hash: bytes,
) -> bool:
    return hmac.compare_digest(
        csrf_token_hash(security_material=security_material, token=token),
        expected_hash,
    )
