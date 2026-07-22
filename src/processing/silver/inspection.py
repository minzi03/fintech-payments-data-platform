"""Payload-safe Silver manifest and Parquet inspection."""

from __future__ import annotations

from collections import Counter

from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.storage import SilverStorage


def manifest_summary(manifest: SqliteProcessingManifest) -> list[dict[str, object]]:
    return [
        {
            "run_id": run.run_id,
            "pipeline_name": run.pipeline_name,
            "source_type": run.source_type.value,
            "entity_name": run.entity_name,
            "input_checksum": run.input_checksum,
            "status": run.status.value,
            "input_record_count": run.input_record_count,
            "output_record_count": run.output_record_count,
            "rejected_record_count": run.rejected_record_count,
            "output_object_uris": run.output_object_uris,
            "code_version": run.code_version,
            "schema_version": run.schema_version,
        }
        for run in manifest.list_all()
    ]


def parquet_summary(storage: SilverStorage, object_uri: str) -> dict[str, object]:
    table = storage.read_table(object_uri)
    summary: dict[str, object] = {
        "row_count": table.num_rows,
        "columns": table.column_names,
        "schema": {field.name: str(field.type) for field in table.schema},
        "schema_metadata": {
            key.decode(): value.decode() for key, value in (table.schema.metadata or {}).items()
        },
    }
    if "is_deleted" in table.column_names:
        summary["deleted_counts"] = dict(
            Counter(str(value).lower() for value in table.column("is_deleted").to_pylist())
        )
    if "error_code" in table.column_names:
        summary["error_counts"] = dict(Counter(table.column("error_code").to_pylist()))
    return summary
