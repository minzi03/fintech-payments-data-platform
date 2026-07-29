"""SQLAlchemy Core engine construction for Portal security state."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from portal_api.core.config import PortalApiSettings
from portal_api.secret_provider import (
    PortalSecretId,
    ResolvedPortalSecrets,
    resolve_environment_secrets,
)


def create_runtime_engine(
    settings: PortalApiSettings,
    *,
    secrets: ResolvedPortalSecrets | None = None,
) -> Engine:
    """Create a bounded runtime engine without connecting or applying migrations."""
    resolved = secrets or resolve_environment_secrets(settings)
    return create_engine(
        resolved.require_text(PortalSecretId.DATABASE_URL),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
    )


def create_audit_worker_engine(
    settings: PortalApiSettings,
    *,
    secrets: ResolvedPortalSecrets | None = None,
) -> Engine:
    """Create the least-privilege worker engine without sharing request pools."""
    resolved = secrets or resolve_environment_secrets(settings)
    return create_engine(
        resolved.require_text(PortalSecretId.AUDIT_WORKER_DATABASE_URL),
        pool_pre_ping=True,
        pool_size=max(2, settings.audit_outbox_worker_concurrency),
        max_overflow=2,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
    )
