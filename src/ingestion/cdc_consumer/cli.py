"""CLI for bounded or long-running reliable CDC Bronze ingestion."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path

from confluent_kafka import Consumer

from common.config import ConfigurationError, StorageSettings
from common.logging import configure_logging
from ingestion.cdc_consumer.config import CdcConsumerSettings
from ingestion.cdc_consumer.consumer import KafkaOffsetCommitter, ReliableCdcConsumer
from ingestion.cdc_consumer.dlq import MinioQuarantineDlq
from ingestion.cdc_consumer.inspection import manifest_summary, parquet_summary
from ingestion.cdc_consumer.manifest import SqliteBatchManifest
from ingestion.cdc_consumer.recovery import CdcBatchProcessor
from ingestion.cdc_consumer.retry import RetryPolicy
from ingestion.cdc_consumer.storage import create_cdc_storage

LOGGER = logging.getLogger(__name__)


def build_parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    environment = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Reliable Kafka CDC to Bronze ingestion")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", description="Consume Debezium events into Bronze Parquet")
    run.add_argument("--topics", help="Comma-separated explicit CDC topic allowlist subset")
    run.add_argument("--group-id")
    run.add_argument("--batch-size", type=int)
    run.add_argument("--flush-interval", type=float)
    run.add_argument("--max-messages", type=int)
    run.add_argument("--once", action="store_true")
    run.add_argument(
        "--storage-backend",
        choices=("local", "minio"),
        default=environment.get("STORAGE_BACKEND", "local"),
    )
    run.add_argument("--dry-run", action="store_true")

    inspect = commands.add_parser(
        "inspect", description="Inspect manifest and Parquet metadata without business payloads"
    )
    inspect.add_argument("--object-uri")
    inspect.add_argument(
        "--storage-backend",
        choices=("local", "minio"),
        default=environment.get("STORAGE_BACKEND", "local"),
    )

    reset = commands.add_parser(
        "reset-state", description="Delete only the local CDC consumer SQLite manifest"
    )
    reset.add_argument("--confirm", action="store_true")
    return parser


def _settings_with_cli(
    settings: CdcConsumerSettings,
    args: argparse.Namespace,
) -> CdcConsumerSettings:
    topics = args.topics.split(",") if args.topics else None
    return settings.with_overrides(
        topics=topics,
        group_id=args.group_id,
        batch_size=args.batch_size,
        flush_interval_seconds=args.flush_interval,
    )


def _storage_settings(
    environ: Mapping[str, str],
    consumer_settings: CdcConsumerSettings,
    backend: str,
) -> StorageSettings:
    settings = StorageSettings.from_env(environ, backend_override=backend)
    return replace(
        settings,
        bronze_bucket=consumer_settings.bronze_bucket,
        quarantine_bucket=consumer_settings.quarantine_bucket,
    )


def _retry_policy(settings: CdcConsumerSettings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.max_retries,
        initial_backoff_seconds=settings.retry_backoff_seconds,
    )


def _run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    settings = _settings_with_cli(CdcConsumerSettings.from_env(environ), args)
    storage = create_cdc_storage(_storage_settings(environ, settings, args.storage_backend))
    manifest = SqliteBatchManifest(settings.manifest_path)
    retry_policy = _retry_policy(settings)
    kafka_consumer = Consumer(settings.kafka_config())
    committer = KafkaOffsetCommitter(kafka_consumer)
    processor = CdcBatchProcessor(
        manifest=manifest,
        storage=storage,
        committer=committer,
        consumer_group=settings.group_id,
        temp_dir=settings.temp_dir,
        retry_policy=retry_policy,
    )
    dlq = MinioQuarantineDlq(
        storage,
        dlq_name=settings.dlq_topic,
        consumer_group=settings.group_id,
        retry_policy=retry_policy,
    )
    service = ReliableCdcConsumer(
        settings=settings,
        processor=processor,
        dlq=dlq,
        consumer=kafka_consumer,
        dry_run=args.dry_run,
    )
    result = service.run(once=args.once, max_messages=args.max_messages)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


def _inspect(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    settings = CdcConsumerSettings.from_env(environ)
    manifest = SqliteBatchManifest(settings.manifest_path)
    payload: dict[str, object] = {"batches": manifest_summary(manifest)}
    if args.object_uri:
        storage = create_cdc_storage(_storage_settings(environ, settings, args.storage_backend))
        payload["parquet"] = parquet_summary(storage, args.object_uri)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _reset(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    settings = CdcConsumerSettings.from_env(environ)
    if not args.confirm:
        raise ValueError(
            "reset-state requires --confirm; Kafka offsets and object data are untouched"
        )
    target = settings.manifest_path.resolve()
    data_root = Path("data").resolve()
    if data_root not in target.parents:
        raise ValueError("Refusing to delete a manifest outside the repository data directory")
    removed: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{target}{suffix}")
        if candidate.is_file():
            candidate.unlink()
            removed.append(candidate.name)
    print(json.dumps({"removed": removed, "kafka_offsets_untouched": True}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
        args = build_parser().parse_args(argv)
        if args.command == "run":
            return _run(args, os.environ)
        if args.command == "inspect":
            return _inspect(args, os.environ)
        return _reset(args, os.environ)
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        LOGGER.error("CDC consumer command failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
