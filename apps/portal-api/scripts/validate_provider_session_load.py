"""Bounded local load validation for provider-session refresh coordination."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import threading
import tracemalloc
from collections import deque
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from portal_api.auth.ports import (
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderOperationResult,
    ProviderOperationStatus,
    ProviderRefreshTokenSet,
    ProviderTokenKind,
)
from portal_api.auth.provider_session import (
    ProviderSessionLifecycleService,
    ProviderTokens,
    RefreshClaim,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment


class LoadRepository:
    """Thread-safe finite queue that detects duplicate refresh finalization."""

    def __init__(self, *, operations: int, batch_size: int) -> None:
        self._batch_size = batch_size
        self._claims = deque(
            RefreshClaim(
                envelope_id=uuid5(NAMESPACE_URL, f"load-envelope-{index}"),
                session_family_id=uuid5(NAMESPACE_URL, f"load-family-{index}"),
                worker_id="unclaimed",
                token_generation=1,
                refresh_failures=0,
                provider_subject=f"load-subject-{index}",
                provider_session=f"load-provider-session-{index}",
                previous_refresh_token_fingerprint=None,
                tokens=ProviderTokens(
                    id_token=None,
                    access_token=f"protected-access-{index}",
                    refresh_token=f"protected-refresh-{index}",
                ),
            )
            for index in range(operations)
        )
        self._lock = threading.Lock()
        self.completed: set[object] = set()
        self.duplicates = 0
        self.failed = 0

    def claim_due(self, *, worker_id: str, **_: object) -> tuple[RefreshClaim, ...]:
        with self._lock:
            claimed: list[RefreshClaim] = []
            while self._claims and len(claimed) < self._batch_size:
                original = self._claims.popleft()
                claimed.append(
                    RefreshClaim(
                        envelope_id=original.envelope_id,
                        session_family_id=original.session_family_id,
                        worker_id=worker_id,
                        token_generation=original.token_generation,
                        refresh_failures=original.refresh_failures,
                        provider_subject=original.provider_subject,
                        provider_session=original.provider_session,
                        previous_refresh_token_fingerprint=None,
                        tokens=original.tokens,
                    )
                )
            return tuple(claimed)

    def complete_refresh(self, *, claim: RefreshClaim, **_: object) -> str:
        with self._lock:
            if claim.envelope_id in self.completed:
                self.duplicates += 1
                return "stale_claim"
            self.completed.add(claim.envelope_id)
        return "rotated"

    def fail_refresh(self, *, claim: RefreshClaim, **_: object) -> str:
        with self._lock:
            self.failed += 1
            self._claims.append(
                RefreshClaim(
                    envelope_id=claim.envelope_id,
                    session_family_id=claim.session_family_id,
                    worker_id="retry-pending",
                    token_generation=claim.token_generation,
                    refresh_failures=claim.refresh_failures + 1,
                    provider_subject=claim.provider_subject,
                    provider_session=claim.provider_session,
                    previous_refresh_token_fingerprint=None,
                    tokens=claim.tokens,
                )
            )
        return "retry_scheduled"


class LoadProvider:
    """Secret-safe provider double with bounded simulated network latency."""

    def __init__(
        self,
        *,
        latency_seconds: float,
        transient_failure_every: int,
    ) -> None:
        self._latency_seconds = latency_seconds
        self._transient_failure_every = transient_failure_every
        self._transient_failed: set[str] = set()
        self.latencies_ms: list[float] = []
        self._active = 0
        self.max_concurrency = 0

    async def refresh_tokens(self, *, refresh_token: str) -> ProviderRefreshTokenSet:
        started = perf_counter()
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        try:
            await asyncio.sleep(self._latency_seconds)
            suffix = refresh_token.rsplit("-", maxsplit=1)[-1]
            if (
                self._transient_failure_every
                and int(suffix) % self._transient_failure_every == 0
                and suffix not in self._transient_failed
            ):
                self._transient_failed.add(suffix)
                raise ProviderExchangeFailure(
                    ProviderFailureKind.AMBIGUOUS,
                    reason_code="PROVIDER_UNAVAILABLE",
                )
            return ProviderRefreshTokenSet(
                access_token=f"rotated-access-{suffix}",
                refresh_token=f"rotated-refresh-{suffix}",
                id_token=None,
                token_type="Bearer",
                expires_in=300,
                refresh_expires_in=1800,
            )
        finally:
            self._active -= 1
            self.latencies_ms.append((perf_counter() - started) * 1000)

    async def revoke_token(
        self,
        *,
        token: str,
        token_kind: ProviderTokenKind,
    ) -> ProviderOperationResult:
        del token, token_kind
        return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)

    async def logout_provider_session(
        self,
        *,
        refresh_token: str | None,
    ) -> ProviderOperationResult:
        del refresh_token
        return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)

    async def front_channel_logout_url(self) -> str | None:
        return None


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


async def execute(args: argparse.Namespace) -> dict[str, int | float]:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_issuer="http://identity.test/realms/portal",
        oidc_client_id="load-client",
        oidc_client_secret="load-client-secret",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
        provider_refresh_enabled=True,
        provider_refresh_batch_size=args.batch_size,
        oidc_http_timeout_seconds=max(1, args.timeout_seconds),
    )
    repository = LoadRepository(operations=args.operations, batch_size=args.batch_size)
    provider = LoadProvider(
        latency_seconds=args.latency_ms / 1000,
        transient_failure_every=args.transient_failure_every,
    )
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=settings,
    )

    async def worker(index: int) -> None:
        while await service.refresh_due(worker_id=f"load-worker-{index}"):
            pass

    tracemalloc.start()
    started = perf_counter()
    await asyncio.gather(*(worker(index) for index in range(args.workers)))
    elapsed = perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    completed = len(repository.completed)
    if completed != args.operations:
        raise RuntimeError(f"Expected {args.operations} completions, observed {completed}")
    if repository.duplicates:
        raise RuntimeError(f"Refresh integrity failed: duplicates={repository.duplicates}")
    p95_ms = percentile(provider.latencies_ms, 0.95)
    peak_mb = peak_bytes / (1024 * 1024)
    if p95_ms > args.max_p95_ms:
        raise RuntimeError(f"Provider refresh p95 {p95_ms:.3f}ms exceeds budget")
    if peak_mb > args.max_peak_mb:
        raise RuntimeError(f"Peak memory {peak_mb:.3f}MiB exceeds budget")

    return {
        "operations": completed,
        "workers": args.workers,
        "batch_size": args.batch_size,
        "duplicates": repository.duplicates,
        "transient_failures_recovered": repository.failed,
        "elapsed_seconds": round(elapsed, 4),
        "throughput_per_second": round(completed / elapsed, 2),
        "provider_latency_mean_ms": round(statistics.fmean(provider.latencies_ms), 4),
        "provider_latency_p95_ms": round(p95_ms, 4),
        "max_provider_concurrency": provider.max_concurrency,
        "peak_memory_mib": round(peak_mb, 4),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--latency-ms", type=float, default=0.25)
    parser.add_argument("--transient-failure-every", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--max-p95-ms", type=float, default=250)
    parser.add_argument("--max-peak-mb", type=float, default=128)
    args = parser.parse_args()
    if (
        args.operations < 1
        or args.workers < 1
        or not 1 <= args.batch_size <= 100
        or args.transient_failure_every < 0
    ):
        parser.error(
            "operations/workers must be positive, batch-size must be 1..100, "
            "and transient-failure-every must not be negative"
        )
    return args


if __name__ == "__main__":
    print(json.dumps(asyncio.run(execute(parse_args())), indent=2, sort_keys=True))
