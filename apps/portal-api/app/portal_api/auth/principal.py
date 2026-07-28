"""Server-authoritative principal and entitlement resolution."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.engine import Engine

from portal_api.auth.ports import (
    PrincipalResolverPort,
    ResolvedPrincipal,
    ValidatedIdentity,
)
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import portal_principals

ROLE_PREFIX = "portal_role:"
ENVIRONMENT_PREFIX = "portal_env:"
MAX_EFFECTIVE_ROLES = 20


class PrincipalResolutionError(ValueError):
    """Fail-closed identity or entitlement resolution failure."""


class ConfiguredPrincipalResolver(PrincipalResolverPort):
    def __init__(self, *, engine: Engine, settings: PortalApiSettings) -> None:
        self._engine = engine
        self._settings = settings

    def resolve(self, identity: ValidatedIdentity) -> ResolvedPrincipal:
        if identity.issuer != self._settings.oidc_issuer:
            raise PrincipalResolutionError("Issuer is not configured")
        principal_id = uuid5(
            NAMESPACE_URL, f"portal-principal:{identity.issuer}\0{identity.subject}"
        )
        with self._engine.connect() as connection:
            existing = connection.execute(
                select(portal_principals.c.status).where(
                    portal_principals.c.issuer == identity.issuer,
                    portal_principals.c.subject_reference == identity.subject,
                )
            ).scalar_one_or_none()
        status = str(existing) if existing is not None else "ACTIVE"
        if status != "ACTIVE":
            raise PrincipalResolutionError("Principal is not active")

        allowed_roles = frozenset(self._settings.oidc_allowed_role_values)
        allowed_environments = frozenset(self._settings.allowed_environment_id_values)
        roles = sorted(
            {
                group.removeprefix(ROLE_PREFIX)
                for group in identity.groups
                if group.startswith(ROLE_PREFIX)
                and group.removeprefix(ROLE_PREFIX) in allowed_roles
            }
        )
        if len(roles) > MAX_EFFECTIVE_ROLES:
            raise PrincipalResolutionError("Effective role count exceeds the accepted bound")
        environments = sorted(
            {
                group.removeprefix(ENVIRONMENT_PREFIX)
                for group in identity.groups
                if group.startswith(ENVIRONMENT_PREFIX)
                and group.removeprefix(ENVIRONMENT_PREFIX) in allowed_environments
            }
        )
        display_attributes = (
            {"display_name": identity.display_name} if identity.display_name is not None else {}
        )
        return ResolvedPrincipal(
            principal_id=principal_id,
            issuer=identity.issuer,
            subject_reference=identity.subject,
            display_attributes=display_attributes,
            status=status,
            roles=tuple(roles),
            environment_ids=tuple(environments),
            tenant_id=self._settings.portal_tenant_id,
            mapping_revision=self._settings.identity_mapping_revision,
            assurance=identity.assurance,
            authenticated_at=identity.authenticated_at,
            token_expires_at=identity.token_expires_at,
            provider_session=identity.provider_session,
        )
