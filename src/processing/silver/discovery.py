"""Backend-neutral incremental Bronze object discovery."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from common.config import StorageSettings
from common.storage import StorageBackend, StoredObject, sha256_file
from processing.silver.models import InputObject, SourceType

ENTITY_ORDER = {
    "customers": 0,
    "merchants": 1,
    "accounts": 2,
    "payment_transactions": 3,
    "transaction_events": 4,
    "refunds": 5,
}


class BronzeDiscovery:
    def __init__(self, backend: StorageBackend, settings: StorageSettings) -> None:
        self._backend = backend
        self._settings = settings

    def discover(
        self,
        *,
        source_type: SourceType,
        input_object: str | None = None,
        input_prefix: str | None = None,
        entity: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        max_objects: int = 100,
    ) -> tuple[InputObject, ...]:
        if input_object:
            candidates = (self.resolve(input_object),)
        else:
            default_prefix = "cdc/" if source_type is SourceType.CDC else "settlements/"
            prefix = input_prefix or default_prefix
            candidates = tuple(
                self._input_from_stored(stored)
                for stored in self._backend.list_objects(self._settings.bronze_bucket, prefix)
            )
        filtered = [
            item
            for item in candidates
            if self._matches(item, source_type, entity, from_date, to_date)
        ]
        filtered.sort(key=lambda item: self._sort_key(item, source_type))
        return tuple(filtered[:max_objects])

    def resolve(self, uri: str) -> InputObject:
        local_candidate = Path(uri)
        if local_candidate.is_absolute():
            return self._resolve_local(local_candidate)
        parsed = urlsplit(uri)
        if parsed.scheme == "s3":
            bucket, object_key = parsed.netloc, parsed.path.lstrip("/")
            stored = self._backend.stat(bucket, object_key)
            if stored is None:
                raise FileNotFoundError("Bronze object does not exist")
            return self._input_from_stored(stored)
        if parsed.scheme:
            raise ValueError("Only s3:// or local Bronze object paths are supported")
        return self._resolve_local(local_candidate)

    def _resolve_local(self, candidate: Path) -> InputObject:
        path = candidate.resolve()
        root = self._settings.local_bronze_root.resolve()
        try:
            object_key = path.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError(
                "Local input object must be under the configured Bronze root"
            ) from error
        stored = self._backend.stat(self._settings.bronze_bucket, object_key)
        if stored is None:
            raise FileNotFoundError("Local Bronze object does not exist")
        return self._input_from_stored(stored)

    def _input_from_stored(self, stored: StoredObject) -> InputObject:
        checksum = stored.checksum_sha256
        if not checksum and not stored.uri.startswith("s3://"):
            checksum = sha256_file(Path(stored.uri))
        return InputObject(
            uri=stored.uri,
            bucket=stored.bucket,
            object_key=stored.object_key,
            checksum_sha256=checksum,
            size_bytes=stored.size_bytes,
            metadata=stored.metadata,
        )

    @staticmethod
    def _matches(
        item: InputObject,
        source_type: SourceType,
        entity: str | None,
        from_date: date | None,
        to_date: date | None,
    ) -> bool:
        key = item.object_key
        if source_type is SourceType.CDC:
            if not key.endswith(".parquet") or not key.startswith("cdc/"):
                return False
            if entity and f"entity={entity}/" not in key:
                return False
            value = _partition_value(key, "event_date")
        else:
            if not key.endswith(".csv") or not key.startswith("settlements/"):
                return False
            value = _partition_value(key, "settlement_date")
        if value:
            try:
                object_date = date.fromisoformat(value)
            except ValueError:
                return False
            if from_date and object_date < from_date:
                return False
            if to_date and object_date > to_date:
                return False
        return True

    @staticmethod
    def _sort_key(item: InputObject, source_type: SourceType) -> tuple[object, ...]:
        if source_type is SourceType.CDC:
            entity = _partition_value(item.object_key, "entity") or ""
            partition = int(_partition_value(item.object_key, "partition") or 0)
            offset = int(_partition_value(item.object_key, "offset_start") or 0)
            return ENTITY_ORDER.get(entity, 99), entity, partition, offset, item.object_key
        return (item.object_key,)


def _partition_value(object_key: str, name: str) -> str | None:
    prefix = f"{name}="
    return next(
        (part.removeprefix(prefix) for part in object_key.split("/") if part.startswith(prefix)),
        None,
    )
