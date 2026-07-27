"""Domain-separated login correlation protection tests."""

from __future__ import annotations

from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose


def test_lookup_protection_is_deterministic_and_domain_separated() -> None:
    material = EphemeralSecurityMaterial.generate()
    value = "browser-visible-random-value"

    state = material.protect(value, purpose=ProtectedPurpose.OIDC_STATE)
    assert state == material.protect(value, purpose=ProtectedPurpose.OIDC_STATE)
    assert state != material.protect(value, purpose=ProtectedPurpose.OIDC_NONCE)
    assert state != material.protect(value, purpose=ProtectedPurpose.BROWSER_BINDING)
    assert value.encode("ascii") not in state


def test_process_security_material_is_not_reused() -> None:
    first = EphemeralSecurityMaterial.generate()
    second = EphemeralSecurityMaterial.generate()

    assert first.protect(
        "same-value",
        purpose=ProtectedPurpose.LOGIN_INTENT,
    ) != second.protect(
        "same-value",
        purpose=ProtectedPurpose.LOGIN_INTENT,
    )
