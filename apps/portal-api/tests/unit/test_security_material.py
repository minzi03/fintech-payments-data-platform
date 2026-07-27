"""Domain-separated login correlation protection tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose

TEST_MASTER_KEY = bytes(range(32))
TEST_ROTATED_MASTER_KEY = bytes(range(32, 64))


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


def test_master_key_material_is_stable_and_version_separated() -> None:
    first = EphemeralSecurityMaterial.from_master_key(
        TEST_MASTER_KEY,
        key_version="local-v1",
    )
    restored = EphemeralSecurityMaterial.from_master_key(
        TEST_MASTER_KEY,
        key_version="local-v1",
    )
    rotated = EphemeralSecurityMaterial.from_master_key(
        TEST_MASTER_KEY,
        key_version="local-v2",
    )

    first_lookup = first.protect("same-value", purpose=ProtectedPurpose.SESSION)
    assert restored.protect("same-value", purpose=ProtectedPurpose.SESSION) == first_lookup
    assert rotated.protect("same-value", purpose=ProtectedPurpose.SESSION) != first_lookup


def test_previous_lookup_is_available_only_inside_bounded_transition() -> None:
    started_at = datetime(2026, 7, 27, 1, tzinfo=UTC)
    expires_at = started_at + timedelta(hours=1)
    previous = EphemeralSecurityMaterial.from_master_key(
        TEST_MASTER_KEY,
        key_version="local-v1",
    )
    current = EphemeralSecurityMaterial.from_master_key(
        TEST_ROTATED_MASTER_KEY,
        key_version="local-v2",
    ).with_previous(
        previous,
        transition_started_at=started_at,
        transition_expires_at=expires_at,
    )

    assert [
        version
        for version, _ in current.protect_candidates(
            "session",
            purpose=ProtectedPurpose.SESSION,
            at=started_at - timedelta(microseconds=1),
        )
    ] == ["local-v2"]
    assert [
        version
        for version, _ in current.protect_candidates(
            "session",
            purpose=ProtectedPurpose.SESSION,
            at=started_at,
        )
    ] == ["local-v2", "local-v1"]
    assert (
        current.material_for_version(
            "local-v1",
            at=expires_at - timedelta(microseconds=1),
        )
        is previous
    )
    assert current.material_for_version("local-v1", at=expires_at) is None
    assert current.material_for_version("unknown-v9", at=started_at) is None
