"""Explicit-schema deterministic Silver Parquet serialization."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common.storage import sha256_file


@dataclass(frozen=True, slots=True)
class SerializedSilver:
    path: Path
    checksum_sha256: str
    size_bytes: int
    record_count: int


def serialize_rows(
    rows: list[dict[str, object]],
    *,
    schema: pa.Schema,
    temp_dir: Path,
    prefix: str,
) -> SerializedSilver:
    temp_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    with tempfile.NamedTemporaryFile(
        prefix=f"silver-{prefix}-", suffix=".parquet", dir=temp_dir, delete=False
    ) as handle:
        path = Path(handle.name)
    try:
        pq.write_table(
            table,
            path,
            compression="zstd",
            version="2.6",
            data_page_version="2.0",
            use_dictionary=True,
            write_statistics=True,
        )
        return SerializedSilver(
            path=path,
            checksum_sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            record_count=table.num_rows,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise


def read_table_bytes(payload: bytes, schema: pa.Schema | None = None) -> pa.Table:
    table = pq.read_table(pa.BufferReader(payload))
    if schema is not None and not table.schema.equals(schema, check_metadata=False):
        raise ValueError("Silver Parquet schema does not match its explicit contract")
    return table
