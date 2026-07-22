"""Payload-safe CDC Bronze and manifest inspection helpers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

from common.storage import sha256_bytes
from ingestion.cdc_consumer.manifest import SqliteBatchManifest
from ingestion.cdc_consumer.storage import CdcObjectStorage


def manifest_summary(manifest: SqliteBatchManifest) -> list[dict[str, object]]:
    """Expose control evidence without customer keys, before/after, or raw payloads."""

    return [
        {
            "batch_id": record.batch_id,
            "consumer_group": record.consumer_group,
            "topic": record.topic,
            "partition": record.partition,
            "offset_start": record.offset_start,
            "offset_end": record.offset_end,
            "status": record.status.value,
            "record_count": record.record_count,
            "checksum_sha256": record.checksum_sha256,
            "object_uri": record.object_uri,
            "retry_count": record.retry_count,
        }
        for record in manifest.list_all()
    ]


def parquet_summary(storage: CdcObjectStorage, object_uri: str) -> dict[str, object]:
    """Read one object and return only schema, operation counts, and coordinates."""

    data = _read_uri(storage, object_uri)
    table = pq.read_table(pa.BufferReader(data))
    operations = Counter(str(value) for value in table.column("operation").to_pylist())
    offsets = [int(value) for value in table.column("kafka_offset").to_pylist()]
    return {
        "row_count": table.num_rows,
        "size_bytes": len(data),
        "checksum_sha256": sha256_bytes(data),
        "columns": table.column_names,
        "operation_counts": dict(sorted(operations.items())),
        "offset_start": min(offsets) if offsets else None,
        "offset_end": max(offsets) if offsets else None,
        "schema_metadata": {
            key.decode(): value.decode() for key, value in (table.schema.metadata or {}).items()
        },
    }


def _read_uri(storage: CdcObjectStorage, object_uri: str) -> bytes:
    local_path = Path(object_uri)
    if local_path.is_file():
        return local_path.read_bytes()
    parsed = urlsplit(object_uri)
    if parsed.scheme == "s3":
        return storage.backend.read_bytes(parsed.netloc, parsed.path.lstrip("/"))
    if parsed.scheme:
        raise ValueError("Only s3:// or local object URIs are supported")
    raise FileNotFoundError("Local CDC Bronze object does not exist")
