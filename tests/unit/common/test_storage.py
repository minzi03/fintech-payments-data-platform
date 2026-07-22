"""Backend-neutral immutable storage contract tests without Docker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from common.storage import (
    ImmutableCollisionError,
    LocalStorageBackend,
    MinioStorageBackend,
    StorageBackendError,
    StoredObject,
    sanitize_metadata,
    sha256_bytes,
    sha256_file,
)


class RecordingMinioClient:
    """Small fake SDK client used through dependency injection."""

    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.get_attempts = 0

    def stat_object(self, bucket_name: str, object_name: str) -> object:
        raise AssertionError(f"Unexpected stat: {bucket_name}/{object_name}")

    def put_object_if_absent(self, **kwargs: object) -> object:
        self.put_calls.append(kwargs)
        return object()

    def get_object(self, bucket_name: str, object_name: str) -> object:
        self.get_attempts += 1
        raise OSError(f"unavailable: {bucket_name}/{object_name}")


def _stored(checksum: str, *, already_exists: bool = False) -> StoredObject:
    return StoredObject(
        bucket="fintech-bronze",
        object_key="settlements/raw.csv",
        uri="s3://fintech-bronze/settlements/raw.csv",
        checksum_sha256=checksum,
        size_bytes=7,
        content_type="text/csv",
        metadata={"checksum_sha256": checksum},
        already_exists=already_exists,
    )


def test_local_backend_is_immutable_and_idempotent(tmp_path: Path) -> None:
    backend = LocalStorageBackend({"bronze": tmp_path / "bronze"})
    source = tmp_path / "source.csv"
    source.write_bytes(b"a,b\n1,2\n")
    checksum = sha256_file(source)

    first = backend.put_immutable(
        bucket="bronze",
        object_key="settlements/source.csv",
        source=source,
        checksum_sha256=checksum,
        content_type="text/csv",
        metadata={"source_name": "banking_partner_settlement"},
    )
    repeated = backend.put_immutable(
        bucket="bronze",
        object_key="settlements/source.csv",
        source=source,
        checksum_sha256=checksum,
        content_type="text/csv",
        metadata={},
    )

    assert backend.read_bytes("bronze", "settlements/source.csv") == source.read_bytes()
    assert first.already_exists is False
    assert repeated.already_exists is True
    assert backend.exists("bronze", "settlements/source.csv")

    metadata_sidecar = Path(first.uri).with_name(f"{Path(first.uri).name}.metadata.json")
    metadata_sidecar.unlink()
    repaired = backend.put_immutable(
        bucket="bronze",
        object_key="settlements/source.csv",
        source=source,
        checksum_sha256=checksum,
        content_type="text/csv",
        metadata={"partner_id": "VCB"},
    )
    assert repaired.already_exists
    assert repaired.metadata["partner_id"] == "VCB"

    changed = tmp_path / "changed.csv"
    changed.write_bytes(b"changed")
    with pytest.raises(ImmutableCollisionError, match="different content"):
        backend.put_immutable(
            bucket="bronze",
            object_key="settlements/source.csv",
            source=changed,
            checksum_sha256=sha256_file(changed),
            content_type="text/csv",
            metadata={},
        )


def test_metadata_allowlist_drops_secrets_paths_and_line_breaks() -> None:
    sanitized = sanitize_metadata(
        {
            "partner_id": "VCB\nforged-header",
            "source_file_name": "settlement.csv",
            "source_path": "C:/private/input.csv",
            "secret_key": "must-not-survive",
        }
    )

    assert sanitized == {
        "partner_id": "VCB forged-header",
        "source_file_name": "settlement.csv",
    }


def test_minio_same_checksum_is_idempotent_and_different_checksum_collides() -> None:
    checksum = sha256_bytes(b"content")
    backend = MinioStorageBackend(RecordingMinioClient(), sleeper=lambda _: None)
    backend.stat = lambda bucket, key: _stored(checksum)  # type: ignore[method-assign]

    repeated = backend.put_bytes_immutable(
        bucket="fintech-bronze",
        object_key="settlements/raw.csv",
        data=b"content",
        content_type="text/csv",
        metadata={},
    )

    assert repeated.already_exists
    with pytest.raises(ImmutableCollisionError, match="different content"):
        backend.put_bytes_immutable(
            bucket="fintech-bronze",
            object_key="settlements/raw.csv",
            data=b"changed",
            content_type="text/csv",
            metadata={},
        )


def test_minio_upload_uses_checksum_metadata(tmp_path: Path) -> None:
    client = RecordingMinioClient()
    backend = MinioStorageBackend(client, sleeper=lambda _: None)
    source = tmp_path / "raw.csv"
    source.write_bytes(b"content")
    checksum = sha256_file(source)
    stats = iter((None, _stored(checksum)))
    backend.stat = lambda bucket, key: next(stats)  # type: ignore[method-assign]

    stored = backend.put_immutable(
        bucket="fintech-bronze",
        object_key="settlements/raw.csv",
        source=source,
        checksum_sha256=checksum,
        content_type="text/csv",
        metadata={"partner_id": "VCB", "secret_key": "dropped"},
    )

    assert stored.checksum_sha256 == checksum
    assert client.put_calls[0]["metadata"] == {
        "checksum-sha256": checksum,
        "partner-id": "VCB",
    }


def test_minio_transport_error_is_bounded_and_mapped() -> None:
    client = RecordingMinioClient()
    backend = MinioStorageBackend(client, max_attempts=2, sleeper=lambda _: None)

    with pytest.raises(StorageBackendError, match="failed after 2 attempts"):
        backend.read_bytes("fintech-bronze", "settlements/raw.csv")

    assert client.get_attempts == 2


def test_minio_stat_normalizes_object_metadata() -> None:
    checksum = "a" * 64

    class StatClient(RecordingMinioClient):
        def stat_object(self, bucket_name: str, object_name: str) -> object:
            return SimpleNamespace(
                size=9,
                content_type="text/csv",
                metadata={
                    "X-Amz-Meta-Checksum-Sha256": checksum,
                    "X-Amz-Meta-Partner-Id": "VCB",
                    "Authorization": "discarded",
                },
            )

    stored = MinioStorageBackend(StatClient()).stat("fintech-bronze", "settlements/raw.csv")

    assert stored is not None
    assert stored.checksum_sha256 == checksum
    assert stored.metadata == {"checksum_sha256": checksum, "partner_id": "VCB"}
