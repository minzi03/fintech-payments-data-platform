"""Deterministic settlement fixture generation for Phase 2 scenarios."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from .contracts import REQUIRED_SETTLEMENT_FIELDS

FIXTURE_NAMESPACE = UUID("9b361f91-fbae-4e98-8cdc-ce8c915f6f9a")
CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class FixtureConfig:
    """Controls for a reproducible family of settlement CSV fixtures."""

    output_dir: Path
    partner_id: str = "VCB"
    settlement_date: date = date(2026, 7, 22)
    seed: int = 42

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", self.partner_id) is None:
            raise ValueError("partner_id must be 2-16 uppercase alphanumeric characters")


def generate_settlement_fixtures(config: FixtureConfig) -> dict[str, Path]:
    """Create deterministic valid, partial-invalid, schema-invalid, and replay fixtures."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    base_name = _file_name(config, 1)
    paths = {
        "valid": config.output_dir / base_name,
        "duplicate_rows": config.output_dir / _file_name(config, 2),
        "invalid_amount": config.output_dir / _file_name(config, 3),
        "invalid_currency": config.output_dir / _file_name(config, 4),
        "invalid_status": config.output_dir / _file_name(config, 5),
        "invalid_schema": config.output_dir / _file_name(config, 6),
        "empty_file": config.output_dir / _file_name(config, 7),
        "changed_content": config.output_dir / "changed_content" / base_name,
    }

    valid_rows = [
        _row(config, "MATCHED", 1),
        _row(config, "MISSING_INTERNAL", 2, internal_transaction_id=""),
        _row(config, "AMOUNT_MISMATCH", 3, amount="125.00"),
        _row(config, "CURRENCY_MISMATCH", 4, currency="EUR"),
        _row(config, "STATUS_MISMATCH", 5, status="FAILED"),
    ]
    _write_csv(paths["valid"], valid_rows)

    duplicate_row = _row(config, "DUPLICATE_SETTLEMENT", 6)
    _write_csv(paths["duplicate_rows"], [duplicate_row, duplicate_row])
    _write_csv(paths["invalid_amount"], [_row(config, "INVALID_AMOUNT", 7, amount="-5.00")])
    _write_csv(paths["invalid_currency"], [_row(config, "INVALID_CURRENCY", 8, currency="usd")])
    _write_csv(paths["invalid_status"], [_row(config, "INVALID_STATUS", 9, status="UNKNOWN")])

    invalid_schema_fields = tuple(
        field for field in REQUIRED_SETTLEMENT_FIELDS if field != "fee_amount"
    )
    invalid_schema_row = _row(config, "INVALID_FILE_SCHEMA", 10)
    _write_csv(paths["invalid_schema"], [invalid_schema_row], invalid_schema_fields)
    _write_csv(paths["empty_file"], [])
    _write_csv(
        paths["changed_content"],
        [_row(config, "SAME_NAME_CHANGED_CONTENT", 11, amount="999.00")],
    )
    return paths


def _file_name(config: FixtureConfig, sequence: int) -> str:
    return f"settlement_{config.partner_id}_{config.settlement_date.isoformat()}_{sequence:03d}.csv"


def _row(
    config: FixtureConfig,
    scenario: str,
    index: int,
    *,
    amount: str = "100.00",
    currency: str = "USD",
    status: str = "SETTLED",
    internal_transaction_id: str | None = None,
) -> dict[str, str]:
    amount_value = Decimal(amount)
    fee = Decimal("1.25")
    net = (amount_value - fee).quantize(CENT)
    transaction_id = internal_transaction_id
    if transaction_id is None:
        transaction_id = str(uuid5(FIXTURE_NAMESPACE, f"{config.seed}:transaction:{index}"))
    timestamp = datetime.combine(
        config.settlement_date,
        time(12, 0),
        tzinfo=UTC,
    ) + timedelta(minutes=index)
    return {
        "partner_id": config.partner_id,
        "settlement_date": config.settlement_date.isoformat(),
        "settlement_reference": f"{scenario}-{config.seed}-{index:04d}",
        "partner_transaction_reference": f"PARTNER-{config.seed}-{index:06d}",
        "internal_transaction_id": transaction_id,
        "transaction_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "amount": f"{amount_value:.2f}",
        "currency": currency,
        "settlement_status": status,
        "fee_amount": f"{fee:.2f}",
        "net_amount": f"{net:.2f}",
    }


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    field_names: tuple[str, ...] = REQUIRED_SETTLEMENT_FIELDS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=field_names,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
