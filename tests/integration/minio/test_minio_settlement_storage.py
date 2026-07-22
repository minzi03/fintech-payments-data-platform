"""Real MinIO acceptance tests for settlement Bronze and quarantine storage."""

from __future__ import annotations

import itertools
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from common.config import StorageSettings
from common.storage import (
    ImmutableCollisionError,
    MinioStorageBackend,
    StorageBackendError,
    sha256_bytes,
    sha256_file,
)
from ingestion.batch.contracts import load_settlement_contract
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from ingestion.batch.manifest import ManifestStore
from ingestion.batch.models import ManifestStatus
from ingestion.batch.settlement_ingestor import SettlementIngestor
from ingestion.batch.storage import SettlementObjectStorage, build_bronze_object_key
from ingestion.batch.storage_factory import create_minio_client, create_storage_backend

pytestmark = [pytest.mark.integration, pytest.mark.minio_integration]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/batch/settlement_v1.yml"
FIXED_NOW = datetime(2026, 7, 23, 6, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def minio_environment():
    if os.getenv("RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("Set RUN_MINIO_INTEGRATION=1 and start MinIO to run these tests")
    settings = StorageSettings.from_env(os.environ, backend_override="minio")
    client = create_minio_client(settings.minio)  # type: ignore[arg-type]
    try:
        assert client.bucket_exists(settings.bronze_bucket)
        assert client.bucket_exists(settings.quarantine_bucket)
    except Exception as error:
        pytest.fail(f"Configured MinIO service or bootstrap buckets are unavailable: {error}")
    backend = create_storage_backend(settings, minio_client=client)
    assert isinstance(backend, MinioStorageBackend)
    return settings, client, backend


def _ingestion_environment(
    tmp_path: Path,
    minio_environment,
    *,
    partner_id: str,
):
    settings, _client, backend = minio_environment
    inbound = tmp_path / "inbound"
    fixtures = generate_settlement_fixtures(
        FixtureConfig(inbound, partner_id, date(2026, 7, 22), 42)
    )
    manifest = ManifestStore(tmp_path / "control/settlement_manifest.sqlite3")
    storage = SettlementObjectStorage(
        backend,
        bronze_bucket=settings.bronze_bucket,
        quarantine_bucket=settings.quarantine_bucket,
    )
    run_counter = itertools.count(1)
    ingestor = SettlementIngestor(
        contract=load_settlement_contract(CONTRACT_PATH),
        manifest=manifest,
        storage=storage,
        clock=lambda: FIXED_NOW,
        run_id_factory=lambda: f"minio-{partner_id.lower()}-{next(run_counter)}",
    )
    return fixtures, manifest, storage, ingestor


def test_bucket_bootstrap_and_valid_raw_upload_with_metadata(
    tmp_path: Path, minio_environment
) -> None:
    settings, _client, backend = minio_environment
    fixtures, manifest, _storage, ingestor = _ingestion_environment(
        tmp_path, minio_environment, partner_id="MIV"
    )

    result = ingestor.ingest_file(fixtures["valid"], expected_partner_id="MIV")

    assert result.status is ManifestStatus.PROCESSED
    assert result.bronze_path is not None
    assert result.bronze_path.startswith(f"s3://{settings.bronze_bucket}/settlements/")
    object_key = result.bronze_path.split(f"s3://{settings.bronze_bucket}/", maxsplit=1)[1]
    assert backend.read_bytes(settings.bronze_bucket, object_key) == fixtures["valid"].read_bytes()
    stored = backend.stat(settings.bronze_bucket, object_key)
    assert stored is not None
    assert stored.checksum_sha256 == sha256_file(fixtures["valid"])
    assert stored.metadata["partner_id"] == "MIV"
    assert stored.metadata["record_count"] == "5"
    persisted = manifest.get(result.file_id or "")
    assert persisted is not None and persisted.bronze_path == result.bronze_path


def test_partial_invalid_uploads_raw_and_rejections_then_rerun_is_idempotent(
    tmp_path: Path, minio_environment
) -> None:
    settings, client, _backend = minio_environment
    fixtures, manifest, _storage, ingestor = _ingestion_environment(
        tmp_path, minio_environment, partner_id="MIP"
    )

    first = ingestor.ingest_file(fixtures["duplicate_rows"], expected_partner_id="MIP")
    repeated = ingestor.ingest_file(fixtures["duplicate_rows"], expected_partner_id="MIP")

    assert first.status is ManifestStatus.PROCESSED
    assert first.bronze_path is not None
    assert first.quarantine_path is not None
    assert repeated.skipped and repeated.status is ManifestStatus.PROCESSED
    assert len(manifest.list_all()) == 1
    bronze_key = first.bronze_path.split(f"s3://{settings.bronze_bucket}/", maxsplit=1)[1]
    assert len(list(client.list_objects(settings.bronze_bucket, prefix=bronze_key))) == 1
    quarantine_key = first.quarantine_path.split(f"s3://{settings.quarantine_bucket}/", maxsplit=1)[
        1
    ]
    rejection_bytes = client.get_object(settings.quarantine_bucket, quarantine_key)
    try:
        assert b"DUPLICATE_ROW" in rejection_bytes.read()
    finally:
        rejection_bytes.close()
        rejection_bytes.release_conn()


def test_invalid_schema_only_writes_quarantine(tmp_path: Path, minio_environment) -> None:
    settings, _client, backend = minio_environment
    fixtures, _manifest, _storage, ingestor = _ingestion_environment(
        tmp_path, minio_environment, partner_id="MIS"
    )

    result = ingestor.ingest_file(fixtures["invalid_schema"], expected_partner_id="MIS")

    assert result.status is ManifestStatus.QUARANTINED
    assert result.bronze_path is None
    assert result.quarantine_path is not None
    possible_bronze_key = build_bronze_object_key(
        partner_id="MIS",
        settlement_date=date(2026, 7, 22),
        ingestion_date=FIXED_NOW.date(),
        checksum_sha256=sha256_file(fixtures["invalid_schema"]),
        file_name=fixtures["invalid_schema"].name,
    )
    assert not backend.exists(settings.bronze_bucket, possible_bronze_key)


def test_minio_collision_never_overwrites(minio_environment) -> None:
    settings, _client, backend = minio_environment
    key = "tests/immutable-collision.txt"

    original = backend.put_bytes_immutable(
        bucket=settings.bronze_bucket,
        object_key=key,
        data=b"immutable-original",
        content_type="text/plain",
        metadata={"artifact_type": "integration_test"},
    )
    with pytest.raises(ImmutableCollisionError):
        backend.put_bytes_immutable(
            bucket=settings.bronze_bucket,
            object_key=key,
            data=b"different-content",
            content_type="text/plain",
            metadata={"artifact_type": "integration_test"},
        )

    assert backend.read_bytes(settings.bronze_bucket, key) == b"immutable-original"
    assert original.checksum_sha256 == sha256_bytes(b"immutable-original")


def test_upload_failure_never_marks_manifest_processed(tmp_path: Path, minio_environment) -> None:
    settings, _client, backend = minio_environment
    fixtures, manifest, _storage, _ingestor = _ingestion_environment(
        tmp_path, minio_environment, partner_id="MIF"
    )

    class FailingStorage(SettlementObjectStorage):
        def copy_to_bronze(self, *args, **kwargs):
            raise StorageBackendError("injected MinIO upload failure")

    failing_storage = FailingStorage(
        backend,
        bronze_bucket=settings.bronze_bucket,
        quarantine_bucket=settings.quarantine_bucket,
    )
    ingestor = SettlementIngestor(
        contract=load_settlement_contract(CONTRACT_PATH),
        manifest=manifest,
        storage=failing_storage,
        clock=lambda: FIXED_NOW,
        run_id_factory=lambda: "minio-failed-upload",
    )

    result = ingestor.ingest_file(fixtures["valid"], expected_partner_id="MIF")

    assert result.status is ManifestStatus.FAILED
    assert result.bronze_path is None
    persisted = manifest.get(result.file_id or "")
    assert persisted is not None
    assert persisted.status is ManifestStatus.FAILED
