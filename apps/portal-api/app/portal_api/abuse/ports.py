"""Infrastructure ports for distributed abuse enforcement."""

from __future__ import annotations

from typing import Protocol

from portal_api.abuse.models import (
    AbuseConcurrencyLease,
    AbuseConcurrencyRequest,
    AbuseEvaluationRequest,
    AbuseEvaluationResult,
)


class AbuseBackendError(RuntimeError):
    """A bounded backend failure without infrastructure details."""


class AbuseBackendUnavailable(AbuseBackendError):
    pass


class AbuseBackendTimeout(AbuseBackendError):
    pass


class AbuseEnforcementPort(Protocol):
    async def evaluate(self, request: AbuseEvaluationRequest) -> AbuseEvaluationResult: ...

    async def acquire_concurrency(
        self,
        request: AbuseConcurrencyRequest,
    ) -> AbuseConcurrencyLease | None: ...

    async def release_concurrency(self, lease: AbuseConcurrencyLease) -> None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...
