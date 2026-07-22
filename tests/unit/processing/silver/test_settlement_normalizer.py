"""Settlement Bronze normalization tests."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from common.storage import sha256_file
from ingestion.batch.contracts import load_settlement_contract
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from processing.silver.models import InputObject
from processing.silver.settlement_normalizer import normalize_settlement_bytes

from .conftest import NOW


def test_settlement_contract_normalizes_decimal_and_partial_rejections(tmp_path: Path) -> None:
    source = generate_settlement_fixtures(
        FixtureConfig(tmp_path / "input", "VCB", date(2026, 7, 22), 42)
    )["duplicate_rows"]
    item = InputObject(
        uri=str(source),
        bucket="fintech-bronze",
        object_key=f"settlements/partner_id=VCB/settlement_date=2026-07-22/{source.name}",
        checksum_sha256=sha256_file(source),
        size_bytes=source.stat().st_size,
        metadata={
            "partner_id": "VCB",
            "source_file_name": source.name,
            "ingestion_run_id": "ingest-1",
        },
    )
    contract = load_settlement_contract(
        Path(__file__).resolve().parents[4] / "contracts" / "batch" / "settlement_v1.yml"
    )

    rows, rejected, count, partner = normalize_settlement_bytes(
        source.read_bytes(),
        input_object=item,
        contract=contract,
        run_id="run-1",
        processed_at=NOW,
        temp_dir=tmp_path / "temp",
    )

    assert count == 2 and partner == "VCB"
    assert len(rows) == 1 and len(rejected) == 1
    assert isinstance(rows[0]["amount"], Decimal)
    assert rows[0]["transaction_timestamp"].tzinfo is not None
    assert "DUPLICATE" not in rejected[0].raw_reference
