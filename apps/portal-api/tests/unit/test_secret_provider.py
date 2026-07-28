"""Vendor-neutral secret-provider lifecycle, rotation, and redaction tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from portal_api.auth.protected_value import AesGcmEnvelopeCipher
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import _security_components, create_app
from portal_api.secret_provider import (
    EnvironmentSecretEntry,
    EnvironmentSecretProvider,
    PortalSecretId,
    ResolvedSecret,
    SecretProviderError,
    SecretProviderFailure,
    SecretProviderStatus,
    SecretPurpose,
    SecretReference,
    SecretValueKind,
    environment_secret_provider,
    portal_secret_catalog,
    resolve_portal_secrets,
)
from pydantic import SecretBytes, SecretStr, ValidationError


def _test_provider_settings(**overrides: object) -> PortalApiSettings:
    started_at = datetime(2026, 7, 28, 1, tzinfo=UTC)
    values: dict[str, object] = {
        "environment": PortalEnvironment.DEVELOPMENT,
        "secret_provider": "test-provider",
        "security_runtime_enabled": True,
        "security_key_version": "rotation-v2",
        "security_previous_key_version": "rotation-v1",
        "security_future_key_version": "rotation-v3",
        "security_key_transition_started_at": started_at,
        "security_key_transition_expires_at": started_at + timedelta(hours=1),
        "oidc_issuer": "https://identity.example/realms/portal",
        "oidc_redirect_uri": "https://portal.example/portal-api/v1/auth/callback",
    }
    values.update(overrides)
    return PortalApiSettings(**values)


def _entry(reference: SecretReference, value: SecretStr | SecretBytes) -> EnvironmentSecretEntry:
    return EnvironmentSecretEntry(version=reference.version, value=value)


def _resolved_test_provider(
    settings: PortalApiSettings,
    *,
    previous_key: bytes = bytes(range(32, 64)),
):
    catalog = portal_secret_catalog(settings)
    references = catalog.references
    entries = {
        (
            references[PortalSecretId.DATABASE_URL].identity,
            references[PortalSecretId.DATABASE_URL].version,
        ): _entry(
            references[PortalSecretId.DATABASE_URL],
            SecretStr("postgresql+psycopg://runtime:placeholder@database/portal"),
        ),
        (
            references[PortalSecretId.OIDC_CLIENT_SECRET].identity,
            references[PortalSecretId.OIDC_CLIENT_SECRET].version,
        ): _entry(
            references[PortalSecretId.OIDC_CLIENT_SECRET],
            SecretStr("synthetic-oidc-value"),
        ),
        (
            references[PortalSecretId.SECURITY_CURRENT_MASTER_KEY].identity,
            references[PortalSecretId.SECURITY_CURRENT_MASTER_KEY].version,
        ): _entry(
            references[PortalSecretId.SECURITY_CURRENT_MASTER_KEY],
            SecretBytes(bytes(range(32))),
        ),
        (
            references[PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY].identity,
            references[PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY].version,
        ): _entry(
            references[PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY],
            SecretBytes(previous_key),
        ),
    }
    provider = EnvironmentSecretProvider(entries, provider_id="test-provider")
    provider.start()
    try:
        return resolve_portal_secrets(settings, provider)
    finally:
        provider.close()


def test_secret_reference_and_value_representations_are_metadata_only() -> None:
    reference = SecretReference(
        identity="portal/security-master-key",
        version="v1",
        purpose=SecretPurpose.SECURITY_MASTER_KEY,
        value_kind=SecretValueKind.BYTES,
        size_bytes=32,
    )
    raw_value = bytes(range(32))
    provider = EnvironmentSecretProvider(
        {
            (reference.identity, reference.version): EnvironmentSecretEntry(
                version=reference.version,
                value=SecretBytes(raw_value),
            )
        }
    )

    provider.start()
    resolved = provider.resolve(reference)
    provider.close()

    rendered = repr(resolved)
    assert resolved.reveal_bytes() == raw_value
    assert raw_value.hex() not in rendered
    assert "**********" in rendered
    assert resolved.metadata.identity == reference.identity


def test_environment_provider_lifecycle_and_failures_are_deterministic() -> None:
    reference = SecretReference(
        identity="portal/oidc-client-secret",
        version="current",
        purpose=SecretPurpose.OIDC_CLIENT_AUTHENTICATION,
        value_kind=SecretValueKind.TEXT,
    )
    provider = EnvironmentSecretProvider({})

    with pytest.raises(SecretProviderError) as not_started:
        provider.resolve(reference)
    assert not_started.value.failure is SecretProviderFailure.NOT_STARTED

    provider.start()
    with pytest.raises(SecretProviderError) as missing:
        provider.resolve(reference)
    assert missing.value.failure is SecretProviderFailure.NOT_FOUND

    other_version = SecretReference(
        identity=reference.identity,
        version="previous",
        purpose=reference.purpose,
        value_kind=reference.value_kind,
    )
    versioned = EnvironmentSecretProvider(
        {
            (other_version.identity, other_version.version): EnvironmentSecretEntry(
                version=other_version.version,
                value=SecretStr("synthetic"),
            )
        }
    )
    versioned.start()
    with pytest.raises(SecretProviderError) as mismatch:
        versioned.resolve(reference)
    assert mismatch.value.failure is SecretProviderFailure.VERSION_MISMATCH
    assert "synthetic" not in str(mismatch.value)


def test_invalid_binary_secret_fails_without_disclosing_value() -> None:
    settings = PortalApiSettings(
        secret_provider="test-provider",
        abuse_protection_enabled=True,
    )
    catalog = portal_secret_catalog(settings)
    hmac_reference = catalog.references[PortalSecretId.CLIENT_ADDRESS_HMAC_KEY]
    redis_reference = catalog.references[PortalSecretId.REDIS_URL]
    provider = EnvironmentSecretProvider(
        {
            (hmac_reference.identity, hmac_reference.version): _entry(
                hmac_reference,
                SecretStr("not-base64-material"),
            ),
            (redis_reference.identity, redis_reference.version): _entry(
                redis_reference,
                SecretStr("redis://localhost:6379/0"),
            ),
        },
        provider_id="test-provider",
    )
    provider.start()

    with pytest.raises(SecretProviderError) as failure:
        resolve_portal_secrets(settings, provider)
    provider.close()

    assert failure.value.failure is SecretProviderFailure.INVALID_VALUE
    assert "not-base64-material" not in str(failure.value)


def test_custom_provider_resolves_exact_versions_and_staged_metadata() -> None:
    settings = _test_provider_settings()
    resolved = _resolved_test_provider(settings)

    assert resolved.require_text(PortalSecretId.DATABASE_URL).startswith("postgresql+psycopg://")
    assert resolved.rotation.current is not None
    assert resolved.rotation.current.version == "rotation-v2"
    assert resolved.rotation.previous is not None
    assert resolved.rotation.previous.version == "rotation-v1"
    assert resolved.rotation.staged_future is not None
    assert resolved.rotation.staged_future.version == "rotation-v3"
    rendered = json.dumps(resolved.evidence.log_fields(), sort_keys=True)
    assert "synthetic-oidc-value" not in rendered
    assert "placeholder@" not in rendered
    assert "rotation-v3" in rendered


def test_rotation_rejects_key_reuse_and_preserves_envelope_compatibility() -> None:
    settings = _test_provider_settings()
    resolved = _resolved_test_provider(settings)
    material, cipher = _security_components(settings, resolved)

    assert material is not None
    assert isinstance(cipher, AesGcmEnvelopeCipher)
    protected = cipher.encrypt(b"provider-token", context=b"session:test")
    assert protected.key_reference == "rotation-v2"
    assert cipher.decrypt(protected, context=b"session:test") == b"provider-token"

    with pytest.raises(SecretProviderError) as reused:
        _resolved_test_provider(settings, previous_key=bytes(range(32)))
    assert reused.value.failure is SecretProviderFailure.CONTINUITY_FAILURE


def test_staged_version_is_unique_and_does_not_load_future_material() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        _test_provider_settings(security_future_key_version="rotation-v2")

    settings = _test_provider_settings()
    catalog = portal_secret_catalog(settings)

    assert catalog.rotation.staged_future is not None
    assert all(reference.version != "rotation-v3" for reference in catalog.references.values())


class _UnavailableProvider:
    provider_id = "test-provider"

    def start(self) -> None:
        pass

    def status(self) -> SecretProviderStatus:
        return SecretProviderStatus(
            provider_id=self.provider_id,
            available=False,
            status_code="offline",
        )

    def resolve(self, reference: SecretReference) -> ResolvedSecret:
        raise AssertionError(f"resolve must not run for unavailable provider: {reference}")

    def close(self) -> None:
        pass


def test_startup_fails_closed_when_provider_is_unavailable() -> None:
    settings = PortalApiSettings(secret_provider="test-provider")

    with pytest.raises(SecretProviderError) as failure:
        create_app(settings=settings, secret_provider=_UnavailableProvider())

    assert failure.value.failure is SecretProviderFailure.UNAVAILABLE
    assert "offline" not in str(failure.value)


def test_startup_accepts_injected_provider_and_retains_only_safe_evidence() -> None:
    settings = PortalApiSettings(secret_provider="test-provider")
    provider = EnvironmentSecretProvider({}, provider_id="test-provider")

    app = create_app(settings=settings, secret_provider=provider)

    evidence = app.state.secret_resolution_evidence
    assert evidence.provider_id == "test-provider"
    assert evidence.references == ()
    assert provider.status().available is False


def test_production_secret_features_reject_environment_provider() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.PRODUCTION,
        openapi_enabled=False,
        log_format="json",
        trusted_hosts="portal.example",
        allowed_origins="https://portal.example",
        build_sha="immutable",
        build_time="2026-07-28T00:00:00Z",
        audit_outbox_enabled=True,
        audit_worker_database_url=("postgresql+psycopg://archive:placeholder@database/portal"),
    )
    provider = environment_secret_provider(settings)
    provider.start()
    with pytest.raises(SecretProviderError) as failure:
        resolve_portal_secrets(settings, provider)
    provider.close()

    assert failure.value.failure is SecretProviderFailure.PRODUCTION_RESTRICTED
