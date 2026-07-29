"""Vendor-neutral Portal startup secret boundary."""

from portal_api.secret_provider.environment import EnvironmentSecretEntry, EnvironmentSecretProvider
from portal_api.secret_provider.models import (
    ResolvedSecret,
    SecretMetadata,
    SecretPurpose,
    SecretReference,
    SecretValueKind,
)
from portal_api.secret_provider.provider import (
    SecretProvider,
    SecretProviderError,
    SecretProviderFailure,
    SecretProviderStatus,
)
from portal_api.secret_provider.runtime import (
    PortalSecretCatalog,
    PortalSecretId,
    ResolvedPortalSecrets,
    SecretResolutionEvidence,
    SecretRotationPlan,
    environment_secret_provider,
    portal_secret_catalog,
    resolve_environment_secrets,
    resolve_portal_secrets,
)

__all__ = [
    "EnvironmentSecretEntry",
    "EnvironmentSecretProvider",
    "PortalSecretCatalog",
    "PortalSecretId",
    "ResolvedPortalSecrets",
    "ResolvedSecret",
    "SecretMetadata",
    "SecretProvider",
    "SecretProviderError",
    "SecretProviderFailure",
    "SecretProviderStatus",
    "SecretPurpose",
    "SecretReference",
    "SecretResolutionEvidence",
    "SecretRotationPlan",
    "SecretValueKind",
    "environment_secret_provider",
    "portal_secret_catalog",
    "resolve_environment_secrets",
    "resolve_portal_secrets",
]
