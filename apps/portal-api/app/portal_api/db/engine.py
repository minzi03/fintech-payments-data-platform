"""SQLAlchemy Core engine construction for Portal security state."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from portal_api.core.config import PortalApiSettings


def create_runtime_engine(settings: PortalApiSettings) -> Engine:
    """Create a bounded runtime engine without connecting or applying migrations."""
    if settings.database_url is None:
        raise RuntimeError("Portal security database URL is not configured")
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
    )


def create_audit_worker_engine(settings: PortalApiSettings) -> Engine:
    """Create the least-privilege worker engine without sharing request pools."""
    if settings.audit_worker_database_url is None:
        raise RuntimeError("Portal audit worker database URL is not configured")
    return create_engine(
        settings.audit_worker_database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=max(2, settings.audit_outbox_worker_concurrency),
        max_overflow=2,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
    )
