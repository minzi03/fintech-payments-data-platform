"""Versioned YAML contract loading for banking partner settlement files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SETTLEMENT_FIELDS = (
    "partner_id",
    "settlement_date",
    "settlement_reference",
    "partner_transaction_reference",
    "internal_transaction_id",
    "transaction_timestamp",
    "amount",
    "currency",
    "settlement_status",
    "fee_amount",
    "net_amount",
)


class ContractError(ValueError):
    """Raised when a settlement contract is missing or internally invalid."""


@dataclass(frozen=True, slots=True)
class FieldContract:
    """Validation metadata for one ordered CSV field."""

    name: str
    data_type: str
    required: bool
    nullable: bool
    precision: int | None = None
    scale: int | None = None
    pattern: str | None = None
    max_length: int | None = None
    allowed_values: tuple[str, ...] = ()
    timezone_required: bool = False


@dataclass(frozen=True, slots=True)
class SettlementContract:
    """The executable subset of one versioned settlement file contract."""

    name: str
    schema_version: str
    contract_version: str
    owner: str
    source_owner: str
    classification: str
    encoding: str
    delimiter: str
    naming_convention: str
    naming_pattern: str
    timestamp_convention: str
    business_key: tuple[str, ...]
    fields: tuple[FieldContract, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    @property
    def allowed_statuses(self) -> tuple[str, ...]:
        field = self.field("settlement_status")
        return field.allowed_values

    def field(self, name: str) -> FieldContract:
        """Return a field by name or fail with contract context."""
        for field in self.fields:
            if field.name == name:
                return field
        raise ContractError(f"Contract {self.schema_version} is missing field {name}")


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{context} must be a mapping")
    return value


def _required_text(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _optional_positive_int(mapping: dict[str, Any], key: str, context: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{context}.{key} must be a positive integer")
    return value


def _parse_field(raw_field: Any, index: int) -> FieldContract:
    context = f"fields[{index}]"
    field = _mapping(raw_field, context)
    allowed_values = field.get("allowed_values", [])
    if not isinstance(allowed_values, list) or not all(
        isinstance(value, str) and value for value in allowed_values
    ):
        raise ContractError(f"{context}.allowed_values must be a string list")

    pattern = field.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ContractError(f"{context}.pattern must be a string")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ContractError(f"{context}.pattern is not valid regex") from error

    return FieldContract(
        name=_required_text(field, "name", context),
        data_type=_required_text(field, "type", context),
        required=field.get("required") is True,
        nullable=field.get("nullable") is True,
        precision=_optional_positive_int(field, "precision", context),
        scale=_optional_positive_int(field, "scale", context),
        pattern=pattern,
        max_length=_optional_positive_int(field, "max_length", context),
        allowed_values=tuple(allowed_values),
        timezone_required=field.get("timezone_required") is True,
    )


def load_settlement_contract(path: Path) -> SettlementContract:
    """Load and validate the executable settlement contract from YAML."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractError(f"Settlement contract does not exist: {path}") from error
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ContractError(f"Settlement contract cannot be read: {path}") from error

    root = _mapping(payload, "contract")
    file_config = _mapping(root.get("file"), "file")
    raw_fields = root.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ContractError("fields must be a non-empty list")
    fields = tuple(_parse_field(field, index) for index, field in enumerate(raw_fields))
    field_names = tuple(field.name for field in fields)
    if field_names != REQUIRED_SETTLEMENT_FIELDS:
        raise ContractError(
            "Settlement fields must exactly match the ordered v1 field list: "
            f"{', '.join(REQUIRED_SETTLEMENT_FIELDS)}"
        )
    if len(set(field_names)) != len(field_names):
        raise ContractError("Settlement field names must be unique")

    business_key = file_config.get("business_key")
    if not isinstance(business_key, list) or not all(
        isinstance(field, str) and field in field_names for field in business_key
    ):
        raise ContractError("file.business_key must reference declared fields")

    delimiter = _required_text(file_config, "delimiter", "file")
    if len(delimiter) != 1:
        raise ContractError("file.delimiter must contain exactly one character")
    naming_pattern = _required_text(file_config, "naming_pattern", "file")
    try:
        compiled_pattern = re.compile(naming_pattern)
    except re.error as error:
        raise ContractError("file.naming_pattern is not valid regex") from error
    expected_groups = {"partner_id", "settlement_date", "sequence"}
    if not expected_groups <= set(compiled_pattern.groupindex):
        raise ContractError("file.naming_pattern must define partner_id, settlement_date, sequence")

    contract = SettlementContract(
        name=_required_text(root, "name", "contract"),
        schema_version=_required_text(root, "schema_version", "contract"),
        contract_version=_required_text(root, "contract_version", "contract"),
        owner=_required_text(root, "owner", "contract"),
        source_owner=_required_text(root, "source_owner", "contract"),
        classification=_required_text(root, "classification", "contract"),
        encoding=_required_text(file_config, "encoding", "file"),
        delimiter=delimiter,
        naming_convention=_required_text(file_config, "naming_convention", "file"),
        naming_pattern=naming_pattern,
        timestamp_convention=_required_text(file_config, "timestamp_convention", "file"),
        business_key=tuple(business_key),
        fields=fields,
    )
    if contract.field("internal_transaction_id").nullable is not True:
        raise ContractError("internal_transaction_id must remain nullable in settlement-v1")
    if not contract.allowed_statuses:
        raise ContractError("settlement_status must declare allowed_values")
    return contract
