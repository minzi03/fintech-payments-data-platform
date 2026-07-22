"""Tests for versioned settlement YAML contract loading."""

from pathlib import Path

import pytest

from ingestion.batch.contracts import (
    REQUIRED_SETTLEMENT_FIELDS,
    ContractError,
    load_settlement_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/batch/settlement_v1.yml"


def test_loads_versioned_contract_with_declared_financial_rules() -> None:
    contract = load_settlement_contract(CONTRACT_PATH)

    assert contract.schema_version == "settlement-v1"
    assert contract.contract_version == "1.0.0"
    assert contract.field_names == REQUIRED_SETTLEMENT_FIELDS
    assert contract.allowed_statuses == ("SETTLED", "REVERSED", "FAILED")
    assert contract.field("amount").precision == 18
    assert contract.field("amount").scale == 2
    assert contract.field("internal_transaction_id").nullable


def test_missing_contract_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="does not exist"):
        load_settlement_contract(tmp_path / "missing.yml")


def test_contract_with_wrong_field_order_is_rejected(tmp_path: Path) -> None:
    invalid_contract = tmp_path / "invalid.yml"
    invalid_contract.write_text(
        """
name: invalid
schema_version: settlement-v1
contract_version: 1.0.0
owner: Finance
source_owner: Partner
classification: confidential
file:
  encoding: utf-8
  delimiter: ','
  naming_convention: invalid
  naming_pattern: '^(?P<partner_id>A)(?P<settlement_date>B)(?P<sequence>C)$'
  timestamp_convention: ISO
  business_key: [partner_id]
fields:
  - name: partner_id
    type: string
    required: true
    nullable: false
""",
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="exactly match"):
        load_settlement_contract(invalid_contract)
