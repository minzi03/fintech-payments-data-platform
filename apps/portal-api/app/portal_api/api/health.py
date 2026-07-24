"""Canonical liveness and readiness endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from portal_api.core.correlation import get_correlation_id
from portal_api.core.errors import PROBLEM_RESPONSES
from portal_api.health.models import (
    LivenessResponse,
    LivenessStatus,
    ReadinessResponse,
    ReadinessStatus,
)

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    operation_id="getLiveness",
    responses=PROBLEM_RESPONSES,
)
async def liveness(request: Request) -> LivenessResponse:
    settings = request.app.state.settings
    return LivenessResponse(
        status=LivenessStatus.UP,
        service=settings.service_name,
        version=settings.service_version,
        build_sha=settings.build_sha,
        time=datetime.now(UTC),
        correlation_id=get_correlation_id(),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    operation_id="getReadiness",
    responses={**PROBLEM_RESPONSES, 503: {"model": ReadinessResponse}},
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    settings = request.app.state.settings
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            dependencies = await request.app.state.health_service.dependency_summaries()
            readiness_status, reason = request.app.state.health_service.readiness(dependencies)
    except TimeoutError:
        dependencies = []
        readiness_status = ReadinessStatus.NOT_READY
        reason = "Readiness evaluation exceeded its configured deadline."
    request.app.state.telemetry.record_readiness(readiness_status.value)
    if readiness_status is ReadinessStatus.NOT_READY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status=readiness_status,
        observed_at=datetime.now(UTC),
        service=settings.service_name,
        version=settings.service_version,
        api_contract_version=settings.contract_version,
        dependencies=dependencies,
        reason=reason,
        correlation_id=get_correlation_id(),
    )
