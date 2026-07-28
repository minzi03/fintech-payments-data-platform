"""Explicit adapter registration without a generic infrastructure proxy."""

from __future__ import annotations

from portal_api.adapters.models import HealthAdapter


class RequiredAdapterConfigurationError(RuntimeError):
    """Raised when a required readiness adapter is absent or not marked required."""

    def __init__(self, adapter_ids: tuple[str, ...]) -> None:
        super().__init__("Required readiness adapters are not configured")
        self.adapter_ids = adapter_ids


class AdapterRegistry:
    """Process-local registry for explicitly supported adapter implementations."""

    def __init__(self, adapters: tuple[HealthAdapter, ...] = ()) -> None:
        self._adapters: dict[str, HealthAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: HealthAdapter) -> None:
        adapter_id = adapter.identity.adapter_id
        if adapter_id in self._adapters:
            raise ValueError(f"Adapter already registered: {adapter_id}")
        self._adapters[adapter_id] = adapter

    def contains(self, adapter_id: str) -> bool:
        return adapter_id in self._adapters

    def missing_required(self, adapter_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            adapter_id
            for adapter_id in adapter_ids
            if adapter_id not in self._adapters or not self._adapters[adapter_id].identity.required
        )

    def validate_required(self, adapter_ids: tuple[str, ...]) -> None:
        missing = self.missing_required(adapter_ids)
        if missing:
            raise RequiredAdapterConfigurationError(missing)

    def all(self) -> tuple[HealthAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))
