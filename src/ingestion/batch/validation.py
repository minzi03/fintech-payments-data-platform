"""File-level and row-level settlement CSV contract validation."""

from __future__ import annotations

import csv
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from .contracts import FieldContract, SettlementContract
from .models import (
    FilenameMetadata,
    FileValidationResult,
    RejectedRecord,
    SettlementRecord,
    ValidationIssue,
)

CENT = Decimal("0.01")


def validate_settlement_file(
    path: Path,
    contract: SettlementContract,
    filename: FilenameMetadata,
    *,
    rejected_at: datetime,
    ingestion_run_id: str,
) -> FileValidationResult:
    """Validate CSV structure and each row while preserving partial acceptance."""
    try:
        with path.open("r", encoding=contract.encoding, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=contract.delimiter, strict=True)
            if reader.fieldnames is None:
                return _file_rejection("EMPTY_FILE", "Settlement file has no header")
            if tuple(reader.fieldnames) != contract.field_names:
                return _file_rejection(
                    "INVALID_FILE_SCHEMA",
                    "CSV header must exactly match the ordered settlement-v1 field list",
                )
            raw_rows = list(reader)
    except UnicodeError:
        return _file_rejection("INVALID_ENCODING", f"File must use {contract.encoding} encoding")
    except (OSError, csv.Error) as error:
        return _file_rejection("INVALID_CSV", f"CSV cannot be parsed: {error}")

    if not raw_rows:
        return _file_rejection("EMPTY_FILE", "Settlement file has a header but no records")

    accepted: list[SettlementRecord] = []
    rejected: list[RejectedRecord] = []
    seen_rows: set[tuple[str | None, ...]] = set()
    seen_references: set[str] = set()

    for row_number, raw_row in enumerate(raw_rows, start=2):
        raw_record = {field: raw_row.get(field) for field in contract.field_names}
        issues: list[ValidationIssue] = []
        fingerprint = tuple(raw_record[field] for field in contract.field_names)
        duplicate_row = fingerprint in seen_rows
        if duplicate_row:
            issues.append(
                ValidationIssue("DUPLICATE_ROW", "Identical row already appeared in file")
            )
        else:
            seen_rows.add(fingerprint)

        settlement_reference = (raw_record.get("settlement_reference") or "").strip()
        if settlement_reference in seen_references and not duplicate_row:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_SETTLEMENT_REFERENCE",
                    "settlement_reference must be unique within one file",
                )
            )
        if settlement_reference:
            seen_references.add(settlement_reference)

        record, record_issues = _validate_row(raw_record, contract, filename, row_number)
        issues.extend(record_issues)
        if issues:
            rejected.append(
                RejectedRecord(
                    source_file=path.name,
                    source_row_number=row_number,
                    raw_record=raw_record,
                    error_code="|".join(dict.fromkeys(issue.code for issue in issues)),
                    error_message="; ".join(issue.message for issue in issues),
                    rejected_at=rejected_at,
                    ingestion_run_id=ingestion_run_id,
                )
            )
        elif record is not None:
            accepted.append(record)

    return FileValidationResult(
        file_issues=(),
        accepted_records=tuple(accepted),
        rejected_records=tuple(rejected),
        record_count=len(raw_rows),
    )


def _validate_row(
    raw: dict[str, str | None],
    contract: SettlementContract,
    filename: FilenameMetadata,
    row_number: int,
) -> tuple[SettlementRecord | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    for field in contract.fields:
        value = raw.get(field.name)
        if value is None:
            if field.required:
                issues.append(
                    ValidationIssue(
                        "REQUIRED_FIELD_MISSING", f"{field.name} column value is missing"
                    )
                )
            continue
        stripped = value.strip()
        if not stripped and not field.nullable:
            issues.append(
                ValidationIssue("REQUIRED_FIELD_EMPTY", f"{field.name} must not be empty")
            )
        if stripped and field.max_length and len(stripped) > field.max_length:
            issues.append(
                ValidationIssue("MAX_LENGTH_EXCEEDED", f"{field.name} exceeds maximum length")
            )

    partner_id = _text(raw, "partner_id")
    if partner_id and not re.fullmatch(contract.field("partner_id").pattern or "", partner_id):
        issues.append(ValidationIssue("INVALID_PARTNER_ID", "partner_id format is invalid"))
    if partner_id and partner_id != filename.partner_id:
        issues.append(
            ValidationIssue("PARTNER_MISMATCH", "partner_id does not match the file name")
        )

    settlement_date = _parse_date(_text(raw, "settlement_date"), issues)
    if settlement_date and settlement_date != filename.settlement_date:
        issues.append(
            ValidationIssue("SETTLEMENT_DATE_MISMATCH", "settlement_date does not match file name")
        )

    settlement_reference = _text(raw, "settlement_reference")
    partner_reference = _text(raw, "partner_transaction_reference")
    internal_transaction_id = _parse_optional_uuid(_text(raw, "internal_transaction_id"), issues)
    transaction_timestamp = _parse_timestamp(_text(raw, "transaction_timestamp"), issues)

    amount = _parse_decimal(_text(raw, "amount"), contract.field("amount"), "amount", issues)
    if amount is not None and amount <= Decimal("0.00"):
        issues.append(ValidationIssue("AMOUNT_NOT_POSITIVE", "amount must be greater than zero"))

    currency = _text(raw, "currency")
    if currency and not re.fullmatch(contract.field("currency").pattern or "", currency):
        issues.append(
            ValidationIssue("INVALID_CURRENCY", "currency must be three uppercase letters")
        )

    status = _text(raw, "settlement_status")
    if status and status not in contract.allowed_statuses:
        issues.append(
            ValidationIssue(
                "INVALID_SETTLEMENT_STATUS",
                f"settlement_status must be one of {', '.join(contract.allowed_statuses)}",
            )
        )

    fee_amount = _parse_decimal(
        _text(raw, "fee_amount"), contract.field("fee_amount"), "fee_amount", issues
    )
    if fee_amount is not None and fee_amount < Decimal("0.00"):
        issues.append(ValidationIssue("NEGATIVE_FEE_AMOUNT", "fee_amount must be zero or greater"))
    net_amount = _parse_decimal(
        _text(raw, "net_amount"), contract.field("net_amount"), "net_amount", issues
    )
    if amount is not None and fee_amount is not None and net_amount is not None:
        expected_net = (amount - fee_amount).quantize(CENT)
        if net_amount != expected_net:
            issues.append(
                ValidationIssue(
                    "NET_AMOUNT_INCONSISTENT",
                    "net_amount must equal amount minus fee_amount",
                )
            )

    required_values = (
        partner_id,
        settlement_date,
        settlement_reference,
        partner_reference,
        transaction_timestamp,
        amount,
        currency,
        status,
        fee_amount,
        net_amount,
    )
    if issues or any(value is None or value == "" for value in required_values):
        return None, issues
    return (
        SettlementRecord(
            partner_id=partner_id,
            settlement_date=settlement_date,
            settlement_reference=settlement_reference,
            partner_transaction_reference=partner_reference,
            internal_transaction_id=internal_transaction_id,
            transaction_timestamp=transaction_timestamp,
            amount=amount,
            currency=currency,
            settlement_status=status,
            fee_amount=fee_amount,
            net_amount=net_amount,
            source_row_number=row_number,
        ),
        issues,
    )


def _text(raw: dict[str, str | None], field: str) -> str:
    return (raw.get(field) or "").strip()


def _parse_date(value: str, issues: list[ValidationIssue]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(ValidationIssue("INVALID_SETTLEMENT_DATE", "settlement_date is not ISO date"))
        return None


def _parse_optional_uuid(value: str, issues: list[ValidationIssue]) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        issues.append(
            ValidationIssue(
                "INVALID_INTERNAL_TRANSACTION_ID", "internal_transaction_id is not UUID"
            )
        )
        return None


def _parse_timestamp(value: str, issues: list[ValidationIssue]) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(
            ValidationIssue(
                "INVALID_TRANSACTION_TIMESTAMP", "transaction_timestamp is not ISO-8601"
            )
        )
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        issues.append(
            ValidationIssue(
                "TIMESTAMP_TIMEZONE_REQUIRED",
                "transaction_timestamp must contain an explicit UTC offset",
            )
        )
        return None
    return parsed.astimezone(UTC)


def _parse_decimal(
    value: str,
    field: FieldContract,
    field_name: str,
    issues: list[ValidationIssue],
) -> Decimal | None:
    if not value:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        issues.append(ValidationIssue("INVALID_DECIMAL", f"{field_name} is not a decimal"))
        return None
    if not parsed.is_finite():
        issues.append(ValidationIssue("INVALID_DECIMAL", f"{field_name} must be finite"))
        return None
    sign, digits, exponent = parsed.as_tuple()
    del sign
    scale = max(-exponent, 0)
    precision = len(digits) + max(exponent, 0)
    if field.scale is not None and scale > field.scale:
        issues.append(
            ValidationIssue("DECIMAL_SCALE_EXCEEDED", f"{field_name} exceeds scale {field.scale}")
        )
        return None
    if field.precision is not None and precision > field.precision:
        issues.append(
            ValidationIssue(
                "DECIMAL_PRECISION_EXCEEDED",
                f"{field_name} exceeds precision {field.precision}",
            )
        )
        return None
    return parsed.quantize(CENT)


def _file_rejection(code: str, message: str) -> FileValidationResult:
    return FileValidationResult(
        file_issues=(ValidationIssue(code, message),),
        accepted_records=(),
        rejected_records=(),
        record_count=0,
    )
