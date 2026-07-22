"""Shared immutable object-storage backends for local filesystems and MinIO."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from minio.error import S3Error
from urllib3.exceptions import HTTPError

T = TypeVar("T")

SAFE_METADATA_FIELDS = frozenset(
    {
        "accepted_count",
        "artifact_type",
        "checksum_sha256",
        "consumer_group",
        "contains_delete",
        "contains_snapshot",
        "contains_tombstone",
        "dlq_topic",
        "entity_name",
        "error_code",
        "ingested_at",
        "ingestion_run_id",
        "kafka_offset",
        "offset_end",
        "offset_start",
        "partner_id",
        "partition",
        "record_count",
        "rejected_count",
        "schema_version",
        "source_file_name",
        "source_file_size",
        "source_name",
        "topic",
    }
)
RETRYABLE_S3_CODES = frozenset(
    {"InternalError", "RequestTimeout", "ServiceUnavailable", "SlowDown"}
)
NOT_FOUND_S3_CODES = frozenset({"NoSuchBucket", "NoSuchKey", "NoSuchObject", "NotFound"})
PRECONDITION_S3_CODES = frozenset({"ConditionalRequestConflict", "PreconditionFailed"})


class StorageBackendError(RuntimeError):
    """Raised when an object-storage operation fails safely."""


class ImmutableCollisionError(StorageBackendError):
    """Raised when an immutable key already contains different content."""


class _ConditionalWriteConflict(StorageBackendError):
    """Internal signal that another writer won an immutable create race."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Backend-neutral object identity and integrity metadata."""

    bucket: str
    object_key: str
    uri: str
    checksum_sha256: str
    size_bytes: int
    content_type: str
    metadata: dict[str, str]
    already_exists: bool = False


class StorageBackend(Protocol):
    """Small immutable-object interface shared by current and future ingestion services."""

    def build_uri(self, bucket: str, object_key: str) -> str: ...

    def exists(self, bucket: str, object_key: str) -> bool: ...

    def stat(self, bucket: str, object_key: str) -> StoredObject | None: ...

    def put_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        checksum_sha256: str,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject: ...

    def put_bytes_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject: ...

    def read_bytes(self, bucket: str, object_key: str) -> bytes: ...


class MinioClient(Protocol):
    """Injectable S3 operations used by the backend."""

    def stat_object(self, bucket_name: str, object_name: str) -> Any: ...

    def put_object_if_absent(
        self,
        *,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> Any: ...

    def get_object(self, bucket_name: str, object_name: str) -> Any: ...


def sha256_bytes(data: bytes) -> str:
    """Return lowercase SHA-256 for in-memory content."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return streaming lowercase SHA-256 for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    """Allow only non-secret ingestion metadata and normalize values for object headers."""
    sanitized: dict[str, str] = {}
    for raw_key, raw_value in metadata.items():
        key = raw_key.strip().lower().replace("-", "_")
        if key not in SAFE_METADATA_FIELDS or raw_value is None:
            continue
        value = str(raw_value).replace("\r", " ").replace("\n", " ").strip()
        if value:
            sanitized[key] = value[:1024]
    return sanitized


def validate_object_key(object_key: str) -> str:
    """Reject absolute, platform-specific, or parent-traversing object keys."""
    if not object_key or "\\" in object_key:
        raise StorageBackendError("Object key must be a non-empty POSIX path")
    path = PurePosixPath(object_key)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise StorageBackendError("Object key must not be absolute or contain traversal segments")
    return path.as_posix()


class LocalStorageBackend:
    """Atomic immutable storage mapped from logical bucket names to local roots."""

    def __init__(self, bucket_roots: Mapping[str, Path]) -> None:
        if not bucket_roots:
            raise ValueError("At least one local bucket root is required")
        self._bucket_roots = dict(bucket_roots)

    def build_uri(self, bucket: str, object_key: str) -> str:
        return str(self._path(bucket, object_key))

    def exists(self, bucket: str, object_key: str) -> bool:
        return self._path(bucket, object_key).is_file()

    def stat(self, bucket: str, object_key: str) -> StoredObject | None:
        path = self._path(bucket, object_key)
        if not path.is_file():
            return None
        metadata_path = self._metadata_path(path)
        metadata: dict[str, str] = {}
        content_type = "application/octet-stream"
        if metadata_path.is_file():
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = sanitize_metadata(payload.get("metadata", {}))
            content_type = str(payload.get("content_type", content_type))
        return StoredObject(
            bucket=bucket,
            object_key=validate_object_key(object_key),
            uri=str(path),
            checksum_sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            content_type=content_type,
            metadata=metadata,
        )

    def put_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        checksum_sha256: str,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject:
        if sha256_file(source) != checksum_sha256:
            raise StorageBackendError("Source checksum does not match the declared SHA-256")
        destination = self._path(bucket, object_key)
        existing = self.stat(bucket, object_key)
        if existing is not None:
            self._resolve_existing(existing, checksum_sha256)
            self._write_metadata(destination, checksum_sha256, content_type, metadata)
            return self._existing_after_metadata(bucket, object_key)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            if sha256_file(temporary) != checksum_sha256:
                raise StorageBackendError("Temporary immutable copy failed checksum verification")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = self.stat(bucket, object_key)
                if existing is None:
                    raise StorageBackendError(
                        "Immutable destination disappeared during write"
                    ) from None
                return self._resolve_existing(existing, checksum_sha256)
        finally:
            temporary.unlink(missing_ok=True)

        self._write_metadata(destination, checksum_sha256, content_type, metadata)
        stored = self.stat(bucket, object_key)
        if stored is None:
            raise StorageBackendError("Immutable local object was not visible after write")
        return stored

    def put_bytes_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject:
        checksum = sha256_bytes(data)
        destination = self._path(bucket, object_key)
        existing = self.stat(bucket, object_key)
        if existing is not None:
            self._resolve_existing(existing, checksum)
            self._write_metadata(destination, checksum, content_type, metadata)
            return self._existing_after_metadata(bucket, object_key)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = self.stat(bucket, object_key)
                if existing is None:
                    raise StorageBackendError(
                        "Immutable destination disappeared during write"
                    ) from None
                return self._resolve_existing(existing, checksum)
        finally:
            temporary.unlink(missing_ok=True)

        self._write_metadata(destination, checksum, content_type, metadata)
        stored = self.stat(bucket, object_key)
        if stored is None:
            raise StorageBackendError("Immutable local object was not visible after write")
        return stored

    def read_bytes(self, bucket: str, object_key: str) -> bytes:
        path = self._path(bucket, object_key)
        if not path.is_file():
            raise StorageBackendError(f"Object does not exist: {bucket}/{object_key}")
        return path.read_bytes()

    def _path(self, bucket: str, object_key: str) -> Path:
        try:
            root = self._bucket_roots[bucket]
        except KeyError as error:
            raise StorageBackendError(f"Unknown local bucket: {bucket}") from error
        return root / Path(*validate_object_key(object_key).split("/"))

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.metadata.json")

    def _write_metadata(
        self,
        destination: Path,
        checksum: str,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> None:
        metadata_path = self._metadata_path(destination)
        metadata_with_checksum = dict(metadata)
        metadata_with_checksum["checksum_sha256"] = checksum
        payload = {
            "checksum_sha256": checksum,
            "content_type": content_type,
            "metadata": sanitize_metadata(metadata_with_checksum),
        }
        content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if metadata_path.exists():
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
            if existing.get("checksum_sha256") != checksum:
                raise ImmutableCollisionError(
                    f"Metadata collision for immutable object: {destination}"
                )
            return
        temporary = metadata_path.with_name(f".{metadata_path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, metadata_path)
            except FileExistsError:
                existing = json.loads(metadata_path.read_text(encoding="utf-8"))
                if existing.get("checksum_sha256") != checksum:
                    raise ImmutableCollisionError(
                        f"Metadata collision for immutable object: {destination}"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _resolve_existing(existing: StoredObject, checksum: str) -> StoredObject:
        if existing.checksum_sha256 != checksum:
            raise ImmutableCollisionError(
                f"Immutable key already exists with different content: {existing.uri}"
            )
        return StoredObject(
            bucket=existing.bucket,
            object_key=existing.object_key,
            uri=existing.uri,
            checksum_sha256=existing.checksum_sha256,
            size_bytes=existing.size_bytes,
            content_type=existing.content_type,
            metadata=existing.metadata,
            already_exists=True,
        )

    def _existing_after_metadata(self, bucket: str, object_key: str) -> StoredObject:
        stored = self.stat(bucket, object_key)
        if stored is None:  # pragma: no cover - protected by the existing-object branch
            raise StorageBackendError("Immutable local object disappeared during metadata repair")
        return StoredObject(
            bucket=stored.bucket,
            object_key=stored.object_key,
            uri=stored.uri,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            content_type=stored.content_type,
            metadata=stored.metadata,
            already_exists=True,
        )


class MinioStorageBackend:
    """Bounded-retry MinIO adapter with conditional immutable object creation."""

    def __init__(
        self,
        client: MinioClient,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.1,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._client = client
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleeper = sleeper

    def build_uri(self, bucket: str, object_key: str) -> str:
        return f"s3://{bucket}/{validate_object_key(object_key)}"

    def exists(self, bucket: str, object_key: str) -> bool:
        return self.stat(bucket, object_key) is not None

    def stat(self, bucket: str, object_key: str) -> StoredObject | None:
        key = validate_object_key(object_key)
        try:
            response = self._retry(
                "stat object", lambda: self._client.stat_object(bucket, key), allow_not_found=True
            )
        except _ObjectNotFound:
            return None
        metadata = _normalize_minio_metadata(getattr(response, "metadata", {}) or {})
        return StoredObject(
            bucket=bucket,
            object_key=key,
            uri=self.build_uri(bucket, key),
            checksum_sha256=metadata.get("checksum_sha256", ""),
            size_bytes=int(getattr(response, "size", 0)),
            content_type=str(getattr(response, "content_type", "application/octet-stream")),
            metadata=metadata,
        )

    def put_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        source: Path,
        checksum_sha256: str,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject:
        if sha256_file(source) != checksum_sha256:
            raise StorageBackendError("Source checksum does not match the declared SHA-256")
        key = validate_object_key(object_key)
        existing = self.stat(bucket, key)
        if existing is not None:
            return self._resolve_existing(existing, checksum_sha256)
        wire_metadata = _minio_wire_metadata(metadata, checksum_sha256)
        try:
            self._retry(
                "upload object",
                lambda: self._client.put_object_if_absent(
                    bucket_name=bucket,
                    object_name=key,
                    data=source.read_bytes(),
                    content_type=content_type,
                    metadata=wire_metadata,
                ),
            )
        except _ConditionalWriteConflict:
            existing = self.stat(bucket, key)
            if existing is None:
                raise StorageBackendError(
                    "Conditional object collision could not be inspected"
                ) from None
            return self._resolve_existing(existing, checksum_sha256)
        return self._verify_upload(bucket, key, checksum_sha256, source.stat().st_size)

    def put_bytes_immutable(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, object],
    ) -> StoredObject:
        key = validate_object_key(object_key)
        checksum = sha256_bytes(data)
        existing = self.stat(bucket, key)
        if existing is not None:
            return self._resolve_existing(existing, checksum)
        wire_metadata = _minio_wire_metadata(metadata, checksum)
        try:
            self._retry(
                "upload object",
                lambda: self._client.put_object_if_absent(
                    bucket_name=bucket,
                    object_name=key,
                    data=data,
                    content_type=content_type,
                    metadata=wire_metadata,
                ),
            )
        except _ConditionalWriteConflict:
            existing = self.stat(bucket, key)
            if existing is None:
                raise StorageBackendError(
                    "Conditional object collision could not be inspected"
                ) from None
            return self._resolve_existing(existing, checksum)
        return self._verify_upload(bucket, key, checksum, len(data))

    def read_bytes(self, bucket: str, object_key: str) -> bytes:
        response = self._retry(
            "read object",
            lambda: self._client.get_object(bucket, validate_object_key(object_key)),
        )
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    def _verify_upload(
        self, bucket: str, object_key: str, checksum: str, expected_size: int
    ) -> StoredObject:
        stored = self.stat(bucket, object_key)
        if stored is None:
            raise StorageBackendError("Uploaded object is not visible")
        if stored.checksum_sha256 != checksum or stored.size_bytes != expected_size:
            raise StorageBackendError("Uploaded object failed checksum or size verification")
        return stored

    def _retry(
        self,
        operation_name: str,
        operation: Callable[[], T],
        *,
        allow_not_found: bool = False,
    ) -> T:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return operation()
            except S3Error as error:
                if allow_not_found and error.code in NOT_FOUND_S3_CODES:
                    raise _ObjectNotFound from error
                if error.code in PRECONDITION_S3_CODES:
                    raise _ConditionalWriteConflict from error
                if error.code not in RETRYABLE_S3_CODES or attempt == self._max_attempts:
                    raise StorageBackendError(
                        f"MinIO {operation_name} failed with code {error.code}"
                    ) from error
            except (HTTPError, OSError) as error:
                if attempt == self._max_attempts:
                    raise StorageBackendError(
                        f"MinIO {operation_name} failed after {attempt} attempts"
                    ) from error
            self._sleeper(self._backoff_seconds * (2 ** (attempt - 1)))
        raise StorageBackendError(f"MinIO {operation_name} exhausted retries")  # pragma: no cover

    @staticmethod
    def _resolve_existing(existing: StoredObject, checksum: str) -> StoredObject:
        if not existing.checksum_sha256 or existing.checksum_sha256 != checksum:
            raise ImmutableCollisionError(
                f"Immutable key already exists with different content: {existing.uri}"
            )
        return StoredObject(
            bucket=existing.bucket,
            object_key=existing.object_key,
            uri=existing.uri,
            checksum_sha256=existing.checksum_sha256,
            size_bytes=existing.size_bytes,
            content_type=existing.content_type,
            metadata=existing.metadata,
            already_exists=True,
        )


class _ObjectNotFound(StorageBackendError):
    """Internal signal for an absent object."""


def _minio_wire_metadata(metadata: Mapping[str, object], checksum_sha256: str) -> dict[str, str]:
    payload = dict(metadata)
    payload["checksum_sha256"] = checksum_sha256
    sanitized = sanitize_metadata(payload)
    return {key.replace("_", "-"): value for key, value in sanitized.items()}


def _normalize_minio_metadata(metadata: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, object] = {}
    for raw_key, raw_value in metadata.items():
        key = str(raw_key).lower()
        if key.startswith("x-amz-meta-"):
            key = key.removeprefix("x-amz-meta-")
        normalized[key.replace("-", "_")] = raw_value
    return sanitize_metadata(normalized)
