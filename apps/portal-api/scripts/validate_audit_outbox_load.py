"""PostgreSQL load, outage, crash-window, and integrity validation for audit delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tracemalloc
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

from portal_api.audit.delivery import PostgresReceiptDestination
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.audit.outbox import AuditOutboxRepository
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
    RetryPolicy,
)
from portal_api.db.unit_of_work import local_transaction
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@dataclass
class ValidationCounters:
    claimed: int = 0
    duplicate_claims: int = 0
    finalized: int = 0
    stale_finalizations: int = 0
    retries: int = 0
    dead_letters: int = 0
    delivery_latencies_ms: list[float] = field(default_factory=list)


class RecoveringDestination:
    """Inject a bounded outage and one poison event before delegating idempotently."""

    def __init__(
        self,
        destination: PostgresReceiptDestination,
        *,
        outage_deliveries: int,
        poison_event_id: UUID,
    ) -> None:
        self._destination = destination
        self._outage_remaining = outage_deliveries
        self._poison_event_id = poison_event_id
        self._lock = asyncio.Lock()

    async def deliver(self, message: AuditDeliveryMessage) -> DeliveryResult:
        async with self._lock:
            if self._outage_remaining:
                self._outage_remaining -= 1
                return DeliveryResult(
                    DeliveryResultKind.RETRYABLE_FAILURE,
                    DeliveryFailureClass.DESTINATION_UNAVAILABLE,
                )
        if message.audit_event_id == self._poison_event_id:
            return DeliveryResult(
                DeliveryResultKind.PERMANENT_FAILURE,
                DeliveryFailureClass.PERMANENT_REJECTION,
            )
        return await self._destination.deliver(message)


def _required_url(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _append_events(
    runtime_engine: Engine,
    operations: int,
) -> tuple[tuple[UUID, ...], list[float]]:
    event_ids: list[UUID] = []
    latencies: list[float] = []
    ledger = AuditLedger()
    with local_transaction(runtime_engine) as connection:
        for index in range(operations):
            event = AuditEvent(
                event_type=AuditEventType.LOGIN_STARTED,
                actor_type="LOAD_TEST",
                outcome="RECORDED",
                correlation_id=f"audit-load-{uuid4()}",
                request_id=f"audit-load-{uuid4()}",
                safe_metadata={
                    "validation_run": "audit_outbox_load",
                    "sequence_bucket": index % 10,
                },
            )
            started = perf_counter()
            ledger.append(connection, event)
            latencies.append((perf_counter() - started) * 1000)
            event_ids.append(event.event_id)
    return tuple(event_ids), latencies


def _prioritize(
    engine: Engine,
    event_ids: tuple[UUID, ...],
    *,
    priority_at: datetime | None = None,
) -> None:
    with local_transaction(engine) as connection:
        connection.execute(
            text(
                "UPDATE portal_control.audit_archive_outbox "
                "SET next_attempt_at = :priority_at "
                "WHERE event_id = ANY(:event_ids)"
            ),
            {
                "event_ids": list(event_ids),
                "priority_at": priority_at or datetime(2000, 1, 1, tzinfo=UTC),
            },
        )


async def _create_crash_windows(
    *,
    archive_engine: Engine,
    event_ids: tuple[UUID, ...],
    delivery_timeout_seconds: float,
) -> int:
    """Leave one claim after delivery and one before delivery, then expire both leases."""
    _prioritize(
        archive_engine,
        event_ids[:2],
        priority_at=datetime(1900, 1, 1, tzinfo=UTC),
    )
    repository = AuditOutboxRepository(archive_engine)
    claims = await asyncio.to_thread(
        repository.claim_batch,
        worker_id="audit-load-crashed-worker",
        batch_size=2,
        lease_seconds=30,
    )
    selected = tuple(message for message in claims if message.audit_event_id in set(event_ids[:2]))
    if len(selected) != 2:
        raise RuntimeError("Could not establish the two deterministic worker crash windows")
    destination = PostgresReceiptDestination(
        archive_engine,
        timeout_seconds=delivery_timeout_seconds,
    )
    result = await destination.deliver(selected[0])
    if result.kind is not DeliveryResultKind.SUCCESS:
        raise RuntimeError("Crash-window pre-delivery did not produce an external side effect")
    with local_transaction(archive_engine) as connection:
        connection.execute(
            text(
                "UPDATE portal_control.audit_archive_outbox "
                "SET lease_expires_at = TIMESTAMPTZ '1900-01-01 00:00:00+00' "
                "WHERE outbox_id = ANY(:outbox_ids)"
            ),
            {"outbox_ids": [message.outbox_id for message in selected]},
        )
    recovered = await asyncio.to_thread(repository.recover_expired_leases, batch_size=2)
    if recovered != 2:
        raise RuntimeError(f"Expected two recovered crash-window leases, observed {recovered}")
    return recovered


async def _drain(
    *,
    archive_engine: Engine,
    worker_count: int,
    batch_size: int,
    lease_seconds: float,
    delivery_timeout_seconds: float,
    outage_deliveries: int,
    poison_event_id: UUID,
    expected_ids: set[UUID],
    deadline_seconds: float,
) -> ValidationCounters:
    counters = ValidationCounters()
    counter_lock = asyncio.Lock()
    active_claims: set[UUID] = set()
    destination = RecoveringDestination(
        PostgresReceiptDestination(
            archive_engine,
            timeout_seconds=delivery_timeout_seconds,
        ),
        outage_deliveries=outage_deliveries,
        poison_event_id=poison_event_id,
    )
    deadline = asyncio.get_running_loop().time() + deadline_seconds

    async def worker(index: int) -> None:
        repository = AuditOutboxRepository(archive_engine)
        retry_policy = RetryPolicy(0.05, 1, jitter_ratio=0)
        while asyncio.get_running_loop().time() < deadline:
            claims = await asyncio.to_thread(
                repository.claim_batch,
                worker_id=f"audit-load-worker-{index}",
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
            if not claims:
                remaining = await asyncio.to_thread(
                    _remaining_count,
                    archive_engine,
                    expected_ids,
                )
                if remaining == 0:
                    return
                await asyncio.sleep(0.01)
                continue
            for message in claims:
                async with counter_lock:
                    if message.outbox_id in active_claims:
                        counters.duplicate_claims += 1
                    active_claims.add(message.outbox_id)
                started = perf_counter()
                try:
                    result = await destination.deliver(message)
                    finalized = await asyncio.to_thread(
                        repository.finalize,
                        message,
                        result,
                        retry_policy=retry_policy,
                    )
                    async with counter_lock:
                        counters.claimed += 1
                        counters.finalized += int(finalized)
                        counters.stale_finalizations += int(not finalized)
                        counters.retries += int(result.kind is DeliveryResultKind.RETRYABLE_FAILURE)
                        counters.dead_letters += int(
                            result.kind is DeliveryResultKind.PERMANENT_FAILURE
                        )
                        counters.delivery_latencies_ms.append((perf_counter() - started) * 1000)
                finally:
                    async with counter_lock:
                        active_claims.discard(message.outbox_id)
        raise TimeoutError("Audit outbox load validation exceeded its deadline")

    await asyncio.gather(*(worker(index) for index in range(worker_count)))
    return counters


def _remaining_count(engine: Engine, event_ids: set[UUID]) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.audit_archive_outbox "
                    "WHERE event_id = ANY(:event_ids) "
                    "AND publication_state NOT IN ('DELIVERED', 'DEAD_LETTERED', 'CANCELLED')"
                ),
                {"event_ids": list(event_ids)},
            ).scalar_one()
        )


def _verify(engine: Engine, event_ids: set[UUID]) -> dict[str, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT "
                "count(*) AS outbox_rows, "
                "count(*) FILTER (WHERE o.publication_state = 'DELIVERED') AS delivered, "
                "count(*) FILTER (WHERE o.publication_state = 'DEAD_LETTERED') AS dead_lettered, "
                "count(r.idempotency_key) AS receipts, "
                "count(DISTINCT r.idempotency_key) AS distinct_receipts "
                "FROM portal_control.audit_archive_outbox o "
                "LEFT JOIN portal_control.audit_delivery_receipts r "
                "ON r.event_id = o.event_id AND r.destination = o.destination "
                "WHERE o.event_id = ANY(:event_ids)"
            ),
            {"event_ids": list(event_ids)},
        ).one()
    return {
        "outbox_rows": int(row.outbox_rows),
        "delivered": int(row.delivered),
        "dead_lettered": int(row.dead_lettered),
        "receipts": int(row.receipts),
        "distinct_receipts": int(row.distinct_receipts),
    }


def _validate_maintenance_load(
    *,
    migration_engine: Engine,
    archive_engine: Engine,
    rows: int,
    batch_size: int,
) -> tuple[int, float]:
    now = datetime.now(UTC)
    expired_ids = tuple(uuid4() for _ in range(rows))
    active_id = uuid4()
    records = [
        {
            "receipt_id": receipt_id,
            "provider_id": "audit-load",
            "jti_hash": b"audit-load-" + receipt_id.bytes,
            "issued_at": now - timedelta(hours=2),
            "expires_at": now - timedelta(minutes=10),
        }
        for receipt_id in expired_ids
    ]
    records.append(
        {
            "receipt_id": active_id,
            "provider_id": "audit-load",
            "jti_hash": b"audit-load-" + active_id.bytes,
            "issued_at": now,
            "expires_at": now + timedelta(minutes=10),
        }
    )
    statement = text(
        "INSERT INTO portal_control.portal_provider_logout_receipts "
        "(receipt_id, provider_id, jti_hash, issued_at, expires_at) "
        "VALUES (:receipt_id, :provider_id, :jti_hash, :issued_at, :expires_at)"
    )
    repository = AuditOutboxRepository(archive_engine, clock=lambda: now)
    processed = 0
    maximum_lock_ms = 0.0
    try:
        with migration_engine.begin() as connection:
            connection.execute(statement, records)
        while processed < rows:
            started = perf_counter()
            removed = repository.cleanup_expired_replays(
                older_than=now - timedelta(minutes=5),
                batch_size=batch_size,
            )
            maximum_lock_ms = max(maximum_lock_ms, (perf_counter() - started) * 1000)
            if removed > batch_size:
                raise RuntimeError("Maintenance cleanup exceeded its bounded batch size")
            if removed == 0:
                break
            processed += removed
        with migration_engine.connect() as connection:
            active_remaining = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.portal_provider_logout_receipts "
                        "WHERE receipt_id = :receipt_id"
                    ),
                    {"receipt_id": active_id},
                ).scalar_one()
            )
        if processed != rows or active_remaining != 1:
            raise RuntimeError(
                "Maintenance load failed its expired/active replay retention boundary"
            )
        return processed, maximum_lock_ms
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM portal_control.portal_provider_logout_receipts "
                    "WHERE receipt_id = ANY(:receipt_ids)"
                ),
                {"receipt_ids": [*expired_ids, active_id]},
            )


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


async def execute(args: argparse.Namespace) -> dict[str, int | float]:
    runtime_engine = create_engine(_required_url("PORTAL_TEST_RUNTIME_DATABASE_URL"))
    archive_engine = create_engine(_required_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    migration_engine = create_engine(_required_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    started = perf_counter()
    tracemalloc.start()
    invariant_violations: list[str] = []
    try:
        event_ids, enqueue_latencies = _append_events(runtime_engine, args.operations)
        expected = set(event_ids)
        lease_recoveries = await _create_crash_windows(
            archive_engine=archive_engine,
            event_ids=event_ids,
            delivery_timeout_seconds=args.delivery_timeout_seconds,
        )
        _prioritize(archive_engine, event_ids)
        drain_started = perf_counter()
        counters = await _drain(
            archive_engine=archive_engine,
            worker_count=args.workers,
            batch_size=args.batch_size,
            lease_seconds=args.lease_seconds,
            delivery_timeout_seconds=args.delivery_timeout_seconds,
            outage_deliveries=args.outage_deliveries,
            poison_event_id=event_ids[-1],
            expected_ids=expected,
            deadline_seconds=args.deadline_seconds,
        )
        backlog_drain_seconds = perf_counter() - drain_started
        verification = _verify(archive_engine, expected)
        maintenance_rows_processed, database_lock_duration_ms = _validate_maintenance_load(
            migration_engine=migration_engine,
            archive_engine=archive_engine,
            rows=args.maintenance_rows,
            batch_size=args.maintenance_batch_size,
        )
    finally:
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        runtime_engine.dispose()
        archive_engine.dispose()
        migration_engine.dispose()

    expected_delivered = args.operations - 1
    checks = {
        "one_outbox_per_event": verification["outbox_rows"] == args.operations,
        "all_non_poison_delivered": verification["delivered"] == expected_delivered,
        "one_poison_dead_lettered": verification["dead_lettered"] == 1,
        "one_receipt_per_delivery": verification["receipts"] == expected_delivered,
        "receipt_idempotency": verification["receipts"] == verification["distinct_receipts"],
        "no_stale_finalization": counters.stale_finalizations == 0,
        "no_duplicate_claim": counters.duplicate_claims == 0,
        "outage_was_exercised": counters.retries >= args.outage_deliveries,
        "crash_leases_recovered": lease_recoveries == 2,
    }
    invariant_violations.extend(name for name, passed in checks.items() if not passed)
    if invariant_violations:
        raise RuntimeError(
            "Audit outbox load invariants failed: " + ", ".join(invariant_violations)
        )

    elapsed = perf_counter() - started
    delivery_latencies = counters.delivery_latencies_ms or [0.0]
    delivery_p95_ms = _percentile(delivery_latencies, 0.95)
    peak_mib = peak_bytes / (1024 * 1024)
    if delivery_p95_ms > args.max_delivery_p95_ms:
        raise RuntimeError(f"Delivery p95 {delivery_p95_ms:.3f}ms exceeds configured budget")
    if peak_mib > args.max_peak_mib:
        raise RuntimeError(f"Peak memory {peak_mib:.3f}MiB exceeds configured budget")

    return {
        "events_enqueued": args.operations,
        "events_delivered": verification["delivered"],
        "retry_count": counters.retries,
        "dead_letter_count": verification["dead_lettered"],
        "duplicate_side_effects": verification["receipts"] - verification["distinct_receipts"],
        "duplicate_claims": counters.duplicate_claims,
        "stale_finalization_rejects": counters.stale_finalizations,
        "backlog_peak": args.operations,
        "backlog_drain_seconds": round(backlog_drain_seconds, 4),
        "enqueue_latency_p50_ms": round(_percentile(enqueue_latencies, 0.50), 4),
        "enqueue_latency_p95_ms": round(_percentile(enqueue_latencies, 0.95), 4),
        "enqueue_latency_p99_ms": round(_percentile(enqueue_latencies, 0.99), 4),
        "delivery_latency_p50_ms": round(_percentile(delivery_latencies, 0.50), 4),
        "delivery_latency_p95_ms": round(delivery_p95_ms, 4),
        "delivery_latency_p99_ms": round(_percentile(delivery_latencies, 0.99), 4),
        "worker_throughput_per_second": round(verification["delivered"] / elapsed, 2),
        "lease_recoveries": lease_recoveries,
        "maintenance_rows_processed": maintenance_rows_processed,
        "database_lock_duration_ms": round(database_lock_duration_ms, 4),
        "peak_memory_mib": round(peak_mib, 4),
        "invariant_violations": len(invariant_violations),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--outage-deliveries", type=int, default=200)
    parser.add_argument("--maintenance-rows", type=int, default=1_000)
    parser.add_argument("--maintenance-batch-size", type=int, default=100)
    parser.add_argument("--lease-seconds", type=float, default=30)
    parser.add_argument("--delivery-timeout-seconds", type=float, default=5)
    parser.add_argument("--deadline-seconds", type=float, default=300)
    parser.add_argument("--max-delivery-p95-ms", type=float, default=250)
    parser.add_argument("--max-peak-mib", type=float, default=256)
    args = parser.parse_args()
    if (
        args.operations < 3
        or args.workers < 1
        or not 1 <= args.batch_size <= 500
        or args.outage_deliveries < 1
        or args.maintenance_rows < 1
        or not 1 <= args.maintenance_batch_size <= 1_000
        or args.lease_seconds <= args.delivery_timeout_seconds
        or args.deadline_seconds <= 0
    ):
        parser.error(
            "operations must be at least 3, worker/outage/maintenance counts must be positive, "
            "delivery batch-size must be 1..500, maintenance batch-size must be 1..1000, "
            "lease must exceed delivery timeout, and deadline must be positive"
        )
    return args


if __name__ == "__main__":
    print(json.dumps(asyncio.run(execute(parse_args())), indent=2, sort_keys=True))
