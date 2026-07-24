"""Safe foundation system metadata and dependency state."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request

from portal_api.core.correlation import get_correlation_id
from portal_api.core.errors import PROBLEM_RESPONSES
from portal_api.health.models import DependencyListResponse, SystemInfoResponse

router = APIRouter(prefix="/v1/system", tags=["system"])


@router.get(
    "/info",
    response_model=SystemInfoResponse,
    operation_id="getSystemInfo",
    responses=PROBLEM_RESPONSES,
)
async def system_info(request: Request) -> SystemInfoResponse:
    settings = request.app.state.settings
    return SystemInfoResponse(
        service_name=settings.service_name,
        service_version=settings.service_version,
        api_contract_version=settings.contract_version,
        build_sha=settings.build_sha,
        build_time=settings.build_time,
        runtime_environment=settings.environment.value,
        supported_api_versions=[settings.api_version],
        documentation_version=settings.documentation_version,
        current_time=datetime.now(UTC),
        correlation_id=get_correlation_id(),
    )


@router.get(
    "/dependencies",
    response_model=DependencyListResponse,
    operation_id="getSystemDependencies",
    responses=PROBLEM_RESPONSES,
)
async def system_dependencies(
    request: Request,
    force: bool = Query(default=False, description="Bypass the short-lived health cache."),
) -> DependencyListResponse:
    dependencies = await request.app.state.health_service.dependency_summaries(force=force)
    return DependencyListResponse(
        observed_at=datetime.now(UTC),
        dependencies=dependencies,
        correlation_id=get_correlation_id(),
    )
