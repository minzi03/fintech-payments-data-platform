"""Upload-before-commit, replay, collision, and crash recovery tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from common.storage import ImmutableCollisionError, LocalStorageBackend
from ingestion.cdc_consumer.manifest import SqliteBatchManifest
from ingestion.cdc_consumer.models import BatchStatus, build_batch
from ingestion.cdc_consumer.recovery import CdcBatchProcessor
from ingestion.cdc_consumer.retry import RetryExhaustedError, RetryPolicy
from ingestion.cdc_consumer.storage import CdcObjectStorage, build_cdc_object_key
from tests.unit.ingestion.cdc_consumer import make_event


@dataclass
class RecordingCommitter:
    fail: bool = False
    commits: list[tuple[str, int, int]] = field(default_factory=list)

    def commit(self, *, topic: str, partition: int, next_offset: int) -> None:
        if self.fail:
            raise ConnectionError("broker unavailable")
        self.commits.append((topic, partition, next_offset))


def components(tmp_path, committer: RecordingCommitter):
    manifest = SqliteBatchManifest(tmp_path / "control" / "manifest.sqlite3")
    backend = LocalStorageBackend(
        {
            "fintech-bronze": tmp_path / "bronze",
            "fintech-quarantine": tmp_path / "quarantine",
        }
    )
    storage = CdcObjectStorage(
        backend,
        bronze_bucket="fintech-bronze",
        quarantine_bucket="fintech-quarantine",
    )
    processor = CdcBatchProcessor(
        manifest=manifest,
        storage=storage,
        committer=committer,
        consumer_group="consumer-a",
        temp_dir=tmp_path / "temp",
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001),
    )
    return manifest, storage, processor


def test_upload_is_verified_before_commit_and_next_offset_is_end_plus_one(tmp_path) -> None:
    committer = RecordingCommitter()
    manifest, storage, processor = components(tmp_path, committer)
    batch = build_batch((make_event(40), make_event(41)))
    result = processor.process(batch)
    record = manifest.get(batch.batch_id)
    assert record is not None and record.status is BatchStatus.COMMITTED
    assert storage.stat_batch(batch) is not None
    assert committer.commits == [(batch.topic, batch.partition, 42)]
    assert result.committed_offset == 42


def test_crash_after_upload_before_commit_replays_same_object(tmp_path) -> None:
    failing = RecordingCommitter(fail=True)
    manifest, storage, first_processor = components(tmp_path, failing)
    batch = build_batch((make_event(50),))
    with pytest.raises(RetryExhaustedError):
        first_processor.process(batch)
    uploaded = manifest.get(batch.batch_id)
    assert uploaded is not None and uploaded.status is BatchStatus.UPLOADED
    first_object = storage.stat_batch(batch)
    assert first_object is not None

    healthy = RecordingCommitter()
    second_processor = CdcBatchProcessor(
        manifest=manifest,
        storage=storage,
        committer=healthy,
        consumer_group="consumer-a",
        temp_dir=tmp_path / "temp",
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001),
    )
    replay = second_processor.process(batch)
    second_object = storage.stat_batch(batch)
    assert replay.replayed
    assert second_object is not None
    assert second_object.checksum_sha256 == first_object.checksum_sha256
    assert healthy.commits[-1][2] == 51
    assert manifest.get(batch.batch_id).status is BatchStatus.COMMITTED  # type: ignore[union-attr]


def test_same_range_different_content_does_not_overwrite(tmp_path) -> None:
    committer = RecordingCommitter()
    manifest, storage, processor = components(tmp_path, committer)
    batch = build_batch((make_event(60),))
    key = build_cdc_object_key(batch)
    storage.backend.put_bytes_immutable(
        bucket="fintech-bronze",
        object_key=key,
        data=b"not the expected parquet",
        content_type="application/octet-stream",
        metadata={},
    )
    with pytest.raises(ImmutableCollisionError):
        processor.process(batch)
    record = manifest.get(batch.batch_id)
    assert record is not None and record.status is BatchStatus.FAILED
    assert committer.commits == []


def test_different_consumer_manifest_reuses_first_ingestion_bytes(tmp_path) -> None:
    first_committer = RecordingCommitter()
    first_manifest, storage, first_processor = components(tmp_path, first_committer)
    batch = build_batch((make_event(65),))
    first_result = first_processor.process(batch)

    second_manifest = SqliteBatchManifest(tmp_path / "control/second.sqlite3")
    second_committer = RecordingCommitter()
    second_processor = CdcBatchProcessor(
        manifest=second_manifest,
        storage=storage,
        committer=second_committer,
        consumer_group="consumer-b",
        temp_dir=tmp_path / "temp",
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001),
    )
    second_result = second_processor.process(batch)
    assert second_result.checksum_sha256 == first_result.checksum_sha256
    assert len(first_manifest.list_all()) == 1
    assert len(second_manifest.list_all()) == 1
    assert second_committer.commits[-1][2] == 66


def test_first_writer_race_rebuilds_once_with_winner_timestamp(tmp_path) -> None:
    first_committer = RecordingCommitter()
    _manifest, storage, first_processor = components(tmp_path, first_committer)
    batch = build_batch((make_event(67),))
    first_result = first_processor.process(batch)

    class MissExistingOnce:
        def __init__(self, delegate):
            self.delegate = delegate
            self.missed = False

        def stat_batch(self, candidate):
            if not self.missed:
                self.missed = True
                return None
            return self.delegate.stat_batch(candidate)

        def put_batch(self, *args, **kwargs):
            return self.delegate.put_batch(*args, **kwargs)

        def verify_batch(self, *args, **kwargs):
            return self.delegate.verify_batch(*args, **kwargs)

    second_manifest = SqliteBatchManifest(tmp_path / "control/race.sqlite3")
    race_storage = MissExistingOnce(storage)
    second_processor = CdcBatchProcessor(
        manifest=second_manifest,
        storage=race_storage,  # type: ignore[arg-type]
        committer=RecordingCommitter(),
        consumer_group="consumer-race",
        temp_dir=tmp_path / "temp",
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001),
    )
    result = second_processor.process(batch)
    assert result.checksum_sha256 == first_result.checksum_sha256
    assert second_manifest.get(batch.batch_id).status is BatchStatus.COMMITTED  # type: ignore[union-attr]


def test_assignment_reconciles_commit_succeeded_manifest_failed_window(tmp_path) -> None:
    committer = RecordingCommitter()
    manifest, _storage, processor = components(tmp_path, committer)
    batch = build_batch((make_event(70),))
    manifest.register(batch, consumer_group="consumer-a")
    manifest.mark_serializing(batch.batch_id)
    manifest.mark_uploading(batch.batch_id)
    manifest.mark_uploaded(
        batch.batch_id,
        checksum_sha256="a" * 64,
        object_uri="s3://fintech-bronze/cdc/existing.parquet",
    )
    repaired = processor.reconcile_committed_offsets({(batch.topic, batch.partition): 71})
    assert repaired == 1
    assert manifest.get(batch.batch_id).status is BatchStatus.COMMITTED  # type: ignore[union-attr]
