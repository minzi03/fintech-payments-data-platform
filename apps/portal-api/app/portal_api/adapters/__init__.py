"""Versioned infrastructure adapter boundaries."""

from portal_api.adapters.models import (
    AdapterError,
    AdapterHealthResult,
    AdapterIdentity,
    AdapterTimeoutError,
    AdapterUnavailableError,
    DependencyStatus,
)
from portal_api.adapters.registry import AdapterRegistry

__all__ = [
    "AdapterError",
    "AdapterHealthResult",
    "AdapterIdentity",
    "AdapterRegistry",
    "AdapterTimeoutError",
    "AdapterUnavailableError",
    "DependencyStatus",
]
