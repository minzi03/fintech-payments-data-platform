"""Migration-only configuration boundary."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from portal_api.configuration.errors import (
    ConfigurationDiagnostic,
    PortalConfigurationError,
)
from portal_api.configuration.models import MigrationRuntimeConfiguration, PortalProcessRole


class MigrationEnvironmentInputs(BaseSettings):
    """Secret migration input isolated from API and worker configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PORTAL_MIGRATION_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    database_url: SecretStr = Field()

    def runtime_configuration(self) -> MigrationRuntimeConfiguration:
        return MigrationRuntimeConfiguration()

    def reveal_validated_database_url(self) -> str:
        value = self.database_url.get_secret_value()
        if not value.startswith("postgresql+psycopg://"):
            raise PortalConfigurationError(
                (
                    ConfigurationDiagnostic(
                        code="PORTAL_CONFIG_MIGRATION_DIALECT",
                        profile="migration",
                        process_role=PortalProcessRole.MIGRATION,
                        field_path="database_url",
                        source="environment",
                        safe_reason="PostgreSQL with psycopg is required",
                    ),
                )
            )
        return value
