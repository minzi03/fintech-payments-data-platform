"""Explicit adapter registration without a generic infrastructure proxy."""

from __future__ import annotations

from portal_api.adapters.models import HealthAdapter


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

    def all(self) -> tuple[HealthAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))
