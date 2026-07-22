"""CDC batch manifest lifecycle and transactional identity tests."""

from __future__ import annotations

import pytest

from ingestion.cdc_consumer.manifest import (
    InvalidManifestTransition,
    ManifestConflictError,
    SqliteBatchManifest,
)
from ingestion.cdc_consumer.models import BatchStatus, build_batch
from tests.unit.ingestion.cdc_consumer import make_event


def test_manifest_follows_full_lifecycle(tmp_path) -> None:
    manifest = SqliteBatchManifest(tmp_path / "manifest.sqlite3")
    batch = build_batch((make_event(1), make_event(2)))
    record = manifest.register(batch, consumer_group="consumer-a")
    assert record.status is BatchStatus.COLLECTING
    assert manifest.mark_serializing(batch.batch_id).status is BatchStatus.SERIALIZING
    assert manifest.mark_uploading(batch.batch_id).status is BatchStatus.UPLOADING
    uploaded = manifest.mark_uploaded(
        batch.batch_id,
        checksum_sha256="a" * 64,
        object_uri="s3://fintech-bronze/cdc/object.parquet",
    )
    assert uploaded.status is BatchStatus.UPLOADED
    assert uploaded.uploaded_at is not None
    committed = manifest.mark_committed(batch.batch_id)
    assert committed.status is BatchStatus.COMMITTED
    assert committed.committed_at is not None


def test_manifest_registration_is_idempotent_and_created_at_is_stable(tmp_path) -> None:
    manifest = SqliteBatchManifest(tmp_path / "manifest.sqlite3")
    batch = build_batch((make_event(5),))
    first = manifest.register(batch, consumer_group="consumer-a")
    second = manifest.register(batch, consumer_group="consumer-a")
    assert first.created_at == second.created_at
    assert len(manifest.list_all()) == 1


def test_manifest_rejects_skipped_or_conflicting_identity(tmp_path) -> None:
    manifest = SqliteBatchManifest(tmp_path / "manifest.sqlite3")
    batch = build_batch((make_event(7),))
    manifest.register(batch, consumer_group="consumer-a")
    with pytest.raises(InvalidManifestTransition):
        manifest.mark_committed(batch.batch_id)
    with pytest.raises(ManifestConflictError):
        manifest.register(batch, consumer_group="consumer-b")


def test_failed_batch_can_retry_but_uploaded_batch_cannot_downgrade(tmp_path) -> None:
    manifest = SqliteBatchManifest(tmp_path / "manifest.sqlite3")
    batch = build_batch((make_event(8),))
    manifest.register(batch, consumer_group="consumer-a")
    failed = manifest.mark_failed(
        batch.batch_id, error_code="SERIALIZATION", error_message="safe failure"
    )
    assert failed.status is BatchStatus.FAILED
    assert failed.retry_count == 1
    assert manifest.mark_serializing(batch.batch_id).status is BatchStatus.SERIALIZING
    manifest.mark_uploading(batch.batch_id)
    manifest.mark_uploaded(
        batch.batch_id,
        checksum_sha256="b" * 64,
        object_uri="s3://fintech-bronze/cdc/object.parquet",
    )
    with pytest.raises(InvalidManifestTransition):
        manifest.mark_failed(batch.batch_id, error_code="LATE", error_message="late")
