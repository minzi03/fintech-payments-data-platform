"""Backend-neutral Bronze discovery and filter tests."""

from datetime import date
from pathlib import Path

import pytest

from common.config import StorageSettings
from common.storage import LocalStorageBackend, StoredObject, sha256_file
from processing.silver.discovery import BronzeDiscovery
from processing.silver.models import SourceType


def _runtime(tmp_path: Path) -> tuple[BronzeDiscovery, LocalStorageBackend, StorageSettings]:
    settings = StorageSettings.from_env(
        {
            "STORAGE_BACKEND": "local",
            "SETTLEMENT_BRONZE_DIR": str(tmp_path / "bronze"),
            "SETTLEMENT_QUARANTINE_DIR": str(tmp_path / "quarantine"),
            "SILVER_LOCAL_ROOT": str(tmp_path / "silver"),
        }
    )
    backend = LocalStorageBackend(
        {
            settings.bronze_bucket: settings.local_bronze_root,
            settings.quarantine_bucket: settings.local_quarantine_root,
            settings.silver_bucket: settings.local_silver_root,
        }
    )
    return BronzeDiscovery(backend, settings), backend, settings


def _put(
    backend: LocalStorageBackend,
    settings: StorageSettings,
    tmp_path: Path,
    key: str,
) -> StoredObject:
    source = tmp_path / f"source-{sha256_file(Path(__file__))[:8]}-{len(key)}.bin"
    source.write_bytes(key.encode())
    return backend.put_immutable(
        bucket=settings.bronze_bucket,
        object_key=key,
        source=source,
        checksum_sha256=sha256_file(source),
        content_type="application/octet-stream",
        metadata={"schema_version": "test-v1"},
    )


def test_discovery_filters_dates_entities_extensions_and_orders_dependencies(
    tmp_path: Path,
) -> None:
    discovery, backend, settings = _runtime(tmp_path)
    keys = (
        "cdc/entity=accounts/event_date=2026-07-22/topic=t/partition=1/offset_start=9/a.parquet",
        "cdc/entity=customers/event_date=2026-07-22/topic=t/partition=0/offset_start=7/c.parquet",
        "cdc/entity=customers/event_date=2026-07-01/topic=t/partition=0/offset_start=1/old.parquet",
        "cdc/entity=customers/event_date=not-a-date/topic=t/partition=0/invalid.parquet",
        "cdc/entity=customers/event_date=2026-07-22/topic=t/partition=0/ignored.json",
        "settlements/partner_id=VCB/settlement_date=2026-07-22/settlement.csv",
        "settlements/partner_id=VCB/settlement_date=2026-07-22/ignored.parquet",
    )
    for key in keys:
        _put(backend, settings, tmp_path, key)

    cdc = discovery.discover(
        source_type=SourceType.CDC,
        from_date=date(2026, 7, 22),
        to_date=date(2026, 7, 22),
        max_objects=2,
    )
    accounts = discovery.discover(
        source_type=SourceType.CDC,
        entity="accounts",
        from_date=date(2026, 7, 22),
    )
    settlements = discovery.discover(source_type=SourceType.SETTLEMENT)

    assert [item.object_key for item in cdc] == [keys[1], keys[0]]
    assert [item.object_key for item in accounts] == [keys[0]]
    assert [item.object_key for item in settlements] == [keys[5]]


def test_resolve_supports_s3_and_local_and_rejects_unsafe_or_missing_paths(tmp_path: Path) -> None:
    discovery, backend, settings = _runtime(tmp_path)
    key = "cdc/entity=customers/event_date=2026-07-22/partition=0/part.parquet"
    stored = _put(backend, settings, tmp_path, key)

    assert discovery.resolve(f"s3://{settings.bronze_bucket}/{key}").object_key == key
    assert discovery.resolve(stored.uri).checksum_sha256 == stored.checksum_sha256
    assert (
        discovery.discover(
            source_type=SourceType.CDC,
            input_object=stored.uri,
        )[0].object_key
        == key
    )

    with pytest.raises(ValueError, match="Only s3"):
        discovery.resolve("https://example.test/object")
    with pytest.raises(ValueError, match="configured Bronze root"):
        discovery.resolve(str(tmp_path / "outside.parquet"))
    with pytest.raises(FileNotFoundError):
        discovery.resolve(f"s3://{settings.bronze_bucket}/missing.parquet")


def test_input_fallback_calculates_checksum_for_local_legacy_object(tmp_path: Path) -> None:
    discovery, _backend, settings = _runtime(tmp_path)
    path = settings.local_bronze_root / "legacy.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"legacy")
    stored = StoredObject(
        uri=str(path),
        bucket=settings.bronze_bucket,
        object_key="legacy.csv",
        checksum_sha256="",
        size_bytes=path.stat().st_size,
        content_type="text/csv",
        metadata={},
    )

    assert discovery._input_from_stored(stored).checksum_sha256 == sha256_file(path)
