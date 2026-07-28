"""Optional Redis abuse-backend readiness reporting."""

from __future__ import annotations

from datetime import UTC, datetime

from portal_api.abuse.ports import AbuseBackendError
from portal_api.abuse.service import AbuseProtectionService
from portal_api.adapters.models import AdapterHealthResult, AdapterIdentity, DependencyStatus

REDIS_ABUSE_ADAPTER_ID = "portal-abuse-redis"


class RedisAbuseReadinessAdapter:
    identity = AdapterIdentity(
        adapter_id=REDIS_ABUSE_ADAPTER_ID,
        display_name="Portal abuse-protection Redis",
        dependency_type="distributed-security-cache",
        required=False,
        version="redis-abuse-v1",
    )

    def __init__(self, service: AbuseProtectionService) -> None:
        self._service = service

    async def check_health(self) -> AdapterHealthResult:
        try:
            healthy = await self._service.ping()
        except AbuseBackendError:
            healthy = False
        return AdapterHealthResult(
            identity=self.identity,
            status=DependencyStatus.UP if healthy else DependencyStatus.DEGRADED,
            observed_at=datetime.now(UTC),
            reason=(
                "Distributed abuse enforcement is available."
                if healthy
                else "Distributed abuse enforcement is degraded; bounded fallbacks are active."
            ),
        )
