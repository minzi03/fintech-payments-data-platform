"""Health and safe system-information response models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from portal_api.adapters.models import DependencyStatus


class LivenessStatus(StrEnum):
    UP = "UP"


class ReadinessStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: LivenessStatus
    service: str
    version: str
    build_sha: str
    time: datetime
    correlation_id: str


class DependencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependency_id: str
    display_name: str
    dependency_type: str
    required: bool
    status: DependencyStatus
    observed_at: datetime
    latency_ms: float | None = Field(default=None, ge=0)
    reason: str | None = None
    runbook_url: str | None = None
    adapter_version: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatus
    observed_at: datetime
    service: str
    version: str
    api_contract_version: str
    dependencies: list[DependencySummary]
    reason: str
    correlation_id: str


class DependencyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    dependencies: list[DependencySummary]
    correlation_id: str


class SystemInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str
    service_version: str
    api_contract_version: str
    build_sha: str
    build_time: str
    runtime_environment: str
    supported_api_versions: list[str]
    documentation_version: str
    current_time: datetime
    correlation_id: str
