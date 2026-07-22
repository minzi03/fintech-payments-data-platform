"""Real MinIO Silver processing fixtures."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ingestion.batch.storage_factory import create_minio_client, create_storage_backend
from processing.silver.bronze_reader import BronzeReader
from processing.silver.config import SilverSettings
from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.processor import SilverProcessor
from processing.silver.storage import SilverStorage


@pytest.fixture(scope="module")
def silver_environment(tmp_path_factory):
    if os.getenv("RUN_SILVER_INTEGRATION") != "1":
        pytest.skip("Set RUN_SILVER_INTEGRATION=1 and start bootstrapped MinIO")
    runtime = Path(tmp_path_factory.mktemp("silver-integration"))
    settings = SilverSettings.from_env(os.environ, backend_override="minio")
    settings = replace(
        settings,
        manifest_path=runtime / "manifest.sqlite3",
        temp_dir=runtime / "temp",
    )
    client = create_minio_client(settings.storage.minio)  # type: ignore[arg-type]
    for bucket in (
        settings.storage.bronze_bucket,
        settings.storage.quarantine_bucket,
        settings.storage.silver_bucket,
    ):
        assert client.bucket_exists(bucket), f"Missing bootstrapped bucket: {bucket}"
    backend = create_storage_backend(settings.storage, minio_client=client)
    manifest = SqliteProcessingManifest(settings.manifest_path)
    storage = SilverStorage(backend, bucket=settings.storage.silver_bucket)
    processor = SilverProcessor(
        settings=settings,
        reader=BronzeReader(backend),
        storage=storage,
        manifest=manifest,
        run_id_factory=lambda: f"silver-it-{uuid4().hex}",
    )
    return settings, backend, client, manifest, storage, processor, runtime
