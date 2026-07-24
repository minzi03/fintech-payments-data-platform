"""Typed adapter and dependency-health contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class DependencyStatus(StrEnum):
    UP = "UP"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PLANNED = "PLANNED"


class AdapterIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    dependency_type: str = Field(min_length=1, max_length=50)
    required: bool = False
    version: str = Field(min_length=1, max_length=50)
    runbook_url: str | None = None


class AdapterHealthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: AdapterIdentity
    status: DependencyStatus
    observed_at: datetime
    latency_ms: float | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=250)


class HealthAdapter(Protocol):
    @property
    def identity(self) -> AdapterIdentity: ...

    async def check_health(self) -> AdapterHealthResult: ...


class AdapterError(RuntimeError):
    """Base adapter failure carrying only a stable dependency ID."""

    def __init__(self, dependency_id: str, message: str = "Dependency check failed") -> None:
        super().__init__(message)
        self.dependency_id = dependency_id


class AdapterTimeoutError(AdapterError):
    pass


class AdapterUnavailableError(AdapterError):
    pass
