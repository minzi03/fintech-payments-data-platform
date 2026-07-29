"""Safe session, CSRF, environment, capability, and navigation contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_reference: UUID
    principal_reference: UUID
    tenant_id: str
    status: str
    roles: list[str]
    environment_ids: list[str]
    assurance: str
    policy_revision: str
    capability_revision: str
    authenticated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


class CsrfView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csrf_token: str
    generation: int


class LogoutResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revoked_session_count: int


class EnvironmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str
    display_name: str
    selected: bool = False


class EnvironmentListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environments: list[EnvironmentView]
    policy_revision: str
    capability_revision: str


class EnvironmentSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str


class EnvironmentSelectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str
    policy_revision: str
    capability_revision: str


class CapabilityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    environment_id: str
    mode: str
    state: str


class CapabilityListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[CapabilityView]
    capability_revision: str


class NavigationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    path: str
    capability_id: str


class NavigationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str
    items: list[NavigationItem]
    policy_revision: str
    capability_revision: str
