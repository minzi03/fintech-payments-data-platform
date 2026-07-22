"""Upload-before-commit protocol and crash recovery for CDC ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from common.storage import ImmutableCollisionError
from ingestion.cdc_consumer.manifest import BatchManifest
from ingestion.cdc_consumer.models import BatchStatus, CdcBatch
from ingestion.cdc_consumer.parquet import cleanup_serialized, serialize_batch
from ingestion.cdc_consumer.retry import RetryPolicy, retry_call
from ingestion.cdc_consumer.storage import CdcObjectStorage


class OffsetCommitter(Protocol):
    """Narrow interface making the Kafka next-offset rule directly testable."""

    def commit(self, *, topic: str, partition: int, next_offset: int) -> None: ...


@dataclass(frozen=True, slots=True)
class BatchProcessResult:
    batch_id: str
    object_uri: str
    checksum_sha256: str
    committed_offset: int
    replayed: bool


class CdcBatchProcessor:
    """Persist a batch durably, then commit exactly ``offset_end + 1``."""

    def __init__(
        self,
        *,
        manifest: BatchManifest,
        storage: CdcObjectStorage,
        committer: OffsetCommitter,
        consumer_group: str,
        temp_dir: Path,
        retry_policy: RetryPolicy,
    ) -> None:
        self._manifest = manifest
        self._storage = storage
        self._committer = committer
        self._consumer_group = consumer_group
        self._temp_dir = Path(temp_dir)
        self._retry_policy = retry_policy

    def process(self, batch: CdcBatch) -> BatchProcessResult:
        record = retry_call(
            lambda: self._manifest.register(batch, consumer_group=self._consumer_group),
            policy=self._retry_policy,
        )
        replayed = record.status in {BatchStatus.UPLOADED, BatchStatus.COMMITTED}

        if record.status in {BatchStatus.UPLOADED, BatchStatus.COMMITTED}:
            if record.checksum_sha256 is None or record.object_uri is None:
                raise RuntimeError("Uploaded manifest record lacks durable object evidence")
            retry_call(
                lambda: self._storage.verify_batch(
                    batch,
                    checksum_sha256=record.checksum_sha256 or "",
                    expected_uri=record.object_uri,
                ),
                policy=self._retry_policy,
            )
            self._commit(batch)
            if record.status is BatchStatus.UPLOADED:
                self._manifest.mark_committed(batch.batch_id)
            return BatchProcessResult(
                batch_id=batch.batch_id,
                object_uri=record.object_uri,
                checksum_sha256=record.checksum_sha256,
                committed_offset=batch.next_offset,
                replayed=True,
            )

        serialized = None
        try:
            retry_call(
                lambda: self._manifest.mark_serializing(batch.batch_id),
                policy=self._retry_policy,
            )
            # A prior consumer group may already have published this source range.
            # Reuse its first-ingestion timestamp so content remains identical even
            # though consumer-group manifests have different creation times.
            stable_record = self._manifest.get(batch.batch_id)
            if stable_record is None:  # pragma: no cover - guarded by registration
                raise RuntimeError("Manifest record disappeared during serialization")
            stable_ingested_at = stable_record.created_at
            existing_object = retry_call(
                lambda: self._storage.stat_batch(batch),
                policy=self._retry_policy,
            )
            if existing_object is not None:
                existing_timestamp = existing_object.metadata.get("ingested_at")
                if existing_timestamp:
                    stable_ingested_at = datetime.fromisoformat(
                        existing_timestamp.replace("Z", "+00:00")
                    )
            serialized = serialize_batch(
                batch,
                temp_dir=self._temp_dir,
                ingested_at=stable_ingested_at,
            )
            retry_call(
                lambda: self._manifest.mark_uploading(batch.batch_id),
                policy=self._retry_policy,
            )
            try:
                stored = retry_call(
                    lambda: self._storage.put_batch(
                        batch,
                        serialized,
                        consumer_group=self._consumer_group,
                        ingested_at=stable_ingested_at,
                    ),
                    policy=self._retry_policy,
                )
            except ImmutableCollisionError:
                # Another group can win the first immutable write between our
                # stat and put. Rebuild once with its first-ingestion timestamp;
                # any remaining byte difference is a genuine collision.
                winner = self._storage.stat_batch(batch)
                winner_timestamp = (
                    winner.metadata.get("ingested_at") if winner is not None else None
                )
                if not winner_timestamp:
                    raise
                stable_ingested_at = datetime.fromisoformat(winner_timestamp.replace("Z", "+00:00"))
                cleanup_serialized(serialized)
                serialized = serialize_batch(
                    batch,
                    temp_dir=self._temp_dir,
                    ingested_at=stable_ingested_at,
                )
                stored = retry_call(
                    lambda: self._storage.put_batch(
                        batch,
                        serialized,
                        consumer_group=self._consumer_group,
                        ingested_at=stable_ingested_at,
                    ),
                    policy=self._retry_policy,
                )
            retry_call(
                lambda: self._manifest.mark_uploaded(
                    batch.batch_id,
                    checksum_sha256=serialized.checksum_sha256,
                    object_uri=stored.uri,
                ),
                policy=self._retry_policy,
            )
        except Exception as exc:
            current = self._manifest.get(batch.batch_id)
            if current is not None and current.status not in {
                BatchStatus.UPLOADED,
                BatchStatus.COMMITTED,
            }:
                self._manifest.mark_failed(
                    batch.batch_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            raise
        finally:
            cleanup_serialized(serialized)

        uploaded = self._manifest.get(batch.batch_id)
        if (
            uploaded is None
            or uploaded.status is not BatchStatus.UPLOADED
            or uploaded.object_uri is None
            or uploaded.checksum_sha256 is None
        ):
            raise RuntimeError("Manifest did not confirm an uploaded CDC batch")

        try:
            self._commit(batch)
        except Exception as exc:
            self._manifest.note_retryable_error(
                batch.batch_id,
                error_code="KAFKA_COMMIT_FAILED",
                error_message=str(exc),
            )
            raise
        # If this final manifest write fails, assignment recovery compares Kafka's
        # committed next offset and promotes UPLOADED without rewriting Bronze.
        retry_call(
            lambda: self._manifest.mark_committed(batch.batch_id),
            policy=self._retry_policy,
        )
        return BatchProcessResult(
            batch_id=batch.batch_id,
            object_uri=uploaded.object_uri,
            checksum_sha256=uploaded.checksum_sha256,
            committed_offset=batch.next_offset,
            replayed=replayed,
        )

    def reconcile_committed_offsets(
        self,
        committed_offsets: dict[tuple[str, int], int],
    ) -> int:
        """Repair UPLOADED manifests after Kafka commit succeeded before status update."""

        repaired = 0
        for record in self._manifest.recoverable(consumer_group=self._consumer_group):
            kafka_next = committed_offsets.get((record.topic, record.partition), -1)
            if record.status is BatchStatus.UPLOADED and kafka_next >= record.offset_end + 1:
                self._manifest.mark_committed(record.batch_id)
                repaired += 1
        return repaired

    def _commit(self, batch: CdcBatch) -> None:
        retry_call(
            lambda: self._committer.commit(
                topic=batch.topic,
                partition=batch.partition,
                next_offset=batch.next_offset,
            ),
            policy=self._retry_policy,
        )
