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
