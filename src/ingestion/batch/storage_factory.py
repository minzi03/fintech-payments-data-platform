"""Configuration-driven construction of local or MinIO settlement storage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from minio import Minio
from urllib3 import PoolManager, Retry, Timeout

from common.config import MinioSettings, StorageBackendKind, StorageSettings
from common.storage import LocalStorageBackend, MinioClient, MinioStorageBackend, StorageBackend

from .storage import SettlementObjectStorage


class MinioSdkClient:
    """Adapt the pinned SDK to the conditional S3 write required by immutability."""

    def __init__(self, client: Minio) -> None:
        self._client = client

    def stat_object(self, bucket_name: str, object_name: str) -> Any:
        return self._client.stat_object(bucket_name, object_name)

    def get_object(self, bucket_name: str, object_name: str) -> Any:
        return self._client.get_object(bucket_name, object_name)

    def put_object_if_absent(
        self,
        *,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> Any:
        headers = {
            "Content-Type": content_type,
            "If-None-Match": "*",
            **{f"X-Amz-Meta-{key}": value for key, value in metadata.items()},
        }
        # MinIO SDK 7.2.20 does not expose destination preconditions on put_object.
        # The pinned low-level PutObject method signs and sends the native S3 headers.
        return self._client._put_object(
            bucket_name,
            object_name,
            data,
            headers=headers,
        )


def create_minio_client(settings: MinioSettings) -> Minio:
    """Create one process-local MinIO client with bounded transport retries and timeouts."""
    http_client = PoolManager(
        timeout=Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
        ),
        retries=Retry(
            total=settings.max_retries,
            connect=settings.max_retries,
            read=settings.max_retries,
            status=settings.max_retries,
            backoff_factor=0.2,
            status_forcelist=(500, 502, 503, 504),
        ),
    )
    return Minio(
        endpoint=settings.endpoint,
        access_key=settings.access_key,
        secret_key=settings.secret_key,
        secure=settings.secure,
        region=settings.region,
        http_client=http_client,
    )


def create_storage_backend(
    settings: StorageSettings,
    *,
    minio_client: MinioClient | None = None,
) -> StorageBackend:
    """Create only the selected backend; local mode never initializes a MinIO client."""
    if settings.backend is StorageBackendKind.LOCAL:
        return LocalStorageBackend(
            {
                settings.bronze_bucket: settings.local_bronze_root,
                settings.quarantine_bucket: settings.local_quarantine_root,
            }
        )
    if settings.minio is None:  # pragma: no cover - protected by StorageSettings
        raise ValueError("MinIO settings are required for the minio backend")
    raw_client = minio_client or create_minio_client(settings.minio)
    client = MinioSdkClient(raw_client) if isinstance(raw_client, Minio) else raw_client
    return MinioStorageBackend(
        client,
        max_attempts=settings.minio.max_retries + 1,
    )


def create_settlement_storage(
    settings: StorageSettings,
    *,
    minio_client: MinioClient | None = None,
) -> SettlementObjectStorage:
    """Bind the selected backend to configured Bronze and quarantine buckets."""
    return SettlementObjectStorage(
        create_storage_backend(settings, minio_client=minio_client),
        bronze_bucket=settings.bronze_bucket,
        quarantine_bucket=settings.quarantine_bucket,
    )
