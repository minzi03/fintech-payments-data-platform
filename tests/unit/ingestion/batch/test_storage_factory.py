"""Tests for storage selection and settlement object-key generation."""

from datetime import date
from pathlib import Path

from common.config import StorageBackendKind, StorageSettings
from common.storage import LocalStorageBackend, MinioStorageBackend
from ingestion.batch.storage import build_bronze_object_key, build_quarantine_object_key
from ingestion.batch.storage_factory import MinioSdkClient, create_storage_backend


class UnusedMinioClient:
    """Identity-only fake; storage calls are outside factory test scope."""


class RecordingSdk:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def _put_object(self, bucket: str, key: str, data: bytes, *, headers):
        self.headers = headers
        return bucket, key, data


def test_storage_factory_defaults_to_local_without_minio_client(tmp_path: Path) -> None:
    settings = StorageSettings(
        backend=StorageBackendKind.LOCAL,
        local_bronze_root=tmp_path / "bronze",
        local_quarantine_root=tmp_path / "quarantine",
        bronze_bucket="fintech-bronze",
        quarantine_bucket="fintech-quarantine",
    )

    assert isinstance(create_storage_backend(settings), LocalStorageBackend)


def test_storage_factory_injects_minio_client() -> None:
    environment = {
        "STORAGE_BACKEND": "minio",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ACCESS_KEY": "test-access",
        "MINIO_SECRET_KEY": "test-secret",
        "MINIO_SECURE": "false",
    }
    settings = StorageSettings.from_env(environment)

    assert isinstance(
        create_storage_backend(settings, minio_client=UnusedMinioClient()),
        MinioStorageBackend,
    )


def test_sdk_adapter_sends_native_conditional_create_header() -> None:
    raw_client = RecordingSdk()
    adapter = MinioSdkClient(raw_client)  # type: ignore[arg-type]

    result = adapter.put_object_if_absent(
        bucket_name="fintech-bronze",
        object_name="settlements/raw.csv",
        data=b"raw",
        content_type="text/csv",
        metadata={"checksum-sha256": "a" * 64},
    )

    assert result == ("fintech-bronze", "settlements/raw.csv", b"raw")
    assert raw_client.headers["If-None-Match"] == "*"
    assert raw_client.headers["X-Amz-Meta-checksum-sha256"] == "a" * 64


def test_settlement_object_keys_are_deterministic() -> None:
    checksum = "a" * 64
    bronze = build_bronze_object_key(
        partner_id="VCB",
        settlement_date=date(2026, 7, 22),
        ingestion_date=date(2026, 7, 23),
        checksum_sha256=checksum,
        file_name="settlement_VCB_2026-07-22_001.csv",
    )
    quarantine = build_quarantine_object_key(
        partner_id="VCB",
        settlement_date=date(2026, 7, 22),
        ingestion_run_id="run-123",
        file_name="settlement_VCB_2026-07-22_001.csv.rejected.jsonl",
    )

    assert bronze == (
        "settlements/partner_id=VCB/settlement_date=2026-07-22/"
        f"ingestion_date=2026-07-23/checksum={checksum}/"
        "settlement_VCB_2026-07-22_001.csv"
    )
    assert quarantine == (
        "settlements/partner_id=VCB/settlement_date=2026-07-22/"
        "ingestion_run_id=run-123/settlement_VCB_2026-07-22_001.csv.rejected.jsonl"
    )
