"""CLI for incremental CDC and settlement Bronze-to-Silver processing."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

from common.config import ConfigurationError
from common.logging import configure_logging
from ingestion.batch.storage_factory import create_storage_backend
from processing.silver.bronze_reader import BronzeReader
from processing.silver.config import SilverSettings
from processing.silver.discovery import BronzeDiscovery
from processing.silver.inspection import manifest_summary, parquet_summary
from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.models import SourceType
from processing.silver.processor import SilverProcessor
from processing.silver.storage import SilverStorage

LOGGER = logging.getLogger(__name__)


def build_parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    environment = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Bronze-to-Silver processing foundation")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("process-cdc", "process-settlements"):
        process = commands.add_parser(command)
        source = process.add_mutually_exclusive_group()
        source.add_argument("--input-object")
        source.add_argument("--input-prefix")
        process.add_argument("--entity")
        process.add_argument("--from-date", type=date.fromisoformat)
        process.add_argument("--to-date", type=date.fromisoformat)
        process.add_argument("--manifest-path", type=Path)
        process.add_argument("--force-reprocess", action="store_true")
        process.add_argument("--dry-run", action="store_true")
        process.add_argument("--max-objects", type=int)
        process.add_argument(
            "--storage-backend",
            choices=("local", "minio"),
            default=environment.get("STORAGE_BACKEND", "local"),
        )

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--object-uri")
    inspect.add_argument("--manifest-path", type=Path)
    inspect.add_argument(
        "--storage-backend",
        choices=("local", "minio"),
        default=environment.get("STORAGE_BACKEND", "local"),
    )
    reset = commands.add_parser("reset-state")
    reset.add_argument("--manifest-path", type=Path)
    reset.add_argument("--confirm", action="store_true")
    return parser


def _runtime(
    environ: Mapping[str, str], backend_name: str, manifest_path: Path | None
) -> tuple[
    SilverSettings, BronzeDiscovery, SilverProcessor, SilverStorage, SqliteProcessingManifest
]:
    settings = SilverSettings.from_env(environ, backend_override=backend_name)
    if manifest_path is not None:
        settings = replace(settings, manifest_path=manifest_path)
    backend = create_storage_backend(settings.storage)
    manifest = SqliteProcessingManifest(settings.manifest_path)
    storage = SilverStorage(backend, bucket=settings.storage.silver_bucket)
    processor = SilverProcessor(
        settings=settings,
        reader=BronzeReader(backend),
        storage=storage,
        manifest=manifest,
    )
    return settings, BronzeDiscovery(backend, settings.storage), processor, storage, manifest


def _process(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    settings, discovery, processor, _storage, _manifest = _runtime(
        environ, args.storage_backend, args.manifest_path
    )
    max_objects = args.max_objects or settings.max_objects
    if max_objects < 1:
        raise ConfigurationError("--max-objects must be greater than zero")
    source_type = SourceType.CDC if args.command == "process-cdc" else SourceType.SETTLEMENT
    objects = discovery.discover(
        source_type=source_type,
        input_object=args.input_object,
        input_prefix=args.input_prefix,
        entity=args.entity,
        from_date=args.from_date,
        to_date=args.to_date,
        max_objects=max_objects,
    )
    results = []
    for item in objects:
        if source_type is SourceType.CDC:
            result = processor.process_cdc(
                item, force_reprocess=args.force_reprocess, dry_run=args.dry_run
            )
        else:
            result = processor.process_settlement(
                item, force_reprocess=args.force_reprocess, dry_run=args.dry_run
            )
        results.append(result.to_dict())
    print(json.dumps({"discovered": len(objects), "results": results}, sort_keys=True))
    return 0 if all(item["status"] == "COMPLETED" for item in results) else 2


def _inspect(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    _settings, _discovery, _processor, storage, manifest = _runtime(
        environ, args.storage_backend, args.manifest_path
    )
    payload: dict[str, object] = {"runs": manifest_summary(manifest)}
    if args.object_uri:
        payload["parquet"] = parquet_summary(storage, args.object_uri)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _reset(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    settings = SilverSettings.from_env(environ)
    target = (args.manifest_path or settings.manifest_path).resolve()
    if not args.confirm:
        raise ValueError("reset-state requires --confirm")
    data_root = Path("data").resolve()
    if data_root not in target.parents:
        raise ValueError("Refusing to delete a manifest outside the repository data directory")
    removed = []
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{target}{suffix}")
        if candidate.is_file():
            candidate.unlink()
            removed.append(candidate.name)
    print(json.dumps({"removed": removed, "bronze_untouched": True}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
        args = build_parser().parse_args(argv)
        if args.command in {"process-cdc", "process-settlements"}:
            return _process(args, os.environ)
        if args.command == "inspect":
            return _inspect(args, os.environ)
        return _reset(args, os.environ)
    except (ConfigurationError, OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Silver command failed: %s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
