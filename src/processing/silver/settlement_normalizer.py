"""Contract-driven normalization of immutable settlement Bronze CSV."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ingestion.batch.contracts import SettlementContract
from ingestion.batch.discovery import DiscoveryError, parse_settlement_filename
from ingestion.batch.validation import validate_settlement_file
from processing.silver.models import InputObject, QualityCode, QualityRejection, utc
from processing.silver.quality import rejection


def normalize_settlement_bytes(
    payload: bytes,
    *,
    input_object: InputObject,
    contract: SettlementContract,
    run_id: str,
    processed_at: datetime,
    temp_dir: Path,
) -> tuple[list[dict[str, object]], list[QualityRejection], int, str]:
    processed_at = utc(processed_at)
    file_name = input_object.metadata.get("source_file_name") or Path(input_object.object_key).name
    partner_id = input_object.metadata.get("partner_id") or _path_partition(
        input_object.object_key, "partner_id"
    )
    work_dir = temp_dir / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    source = work_dir / Path(file_name).name
    source.write_bytes(payload)
    try:
        try:
            filename = parse_settlement_filename(source, contract.naming_pattern, partner_id)
        except DiscoveryError as error:
            return (
                [],
                [
                    rejection(
                        source_object_uri=input_object.uri,
                        source_event_id=None,
                        entity_name="settlements",
                        business_key=None,
                        code=QualityCode.INVALID_BRONZE_SCHEMA,
                        message=str(error),
                        raw_reference=f"file:{file_name}",
                        run_id=run_id,
                        rejected_at=processed_at,
                    )
                ],
                0,
                partner_id,
            )
        result = validate_settlement_file(
            source,
            contract,
            filename,
            rejected_at=processed_at,
            ingestion_run_id=input_object.metadata.get("ingestion_run_id", "unknown"),
        )
        quality: list[QualityRejection] = []
        for issue in result.file_issues:
            quality.append(
                rejection(
                    source_object_uri=input_object.uri,
                    source_event_id=None,
                    entity_name="settlements",
                    business_key=None,
                    code=QualityCode.INVALID_BRONZE_SCHEMA,
                    message=f"{issue.code}: {issue.message}",
                    raw_reference=f"file:{file_name}",
                    run_id=run_id,
                    rejected_at=processed_at,
                )
            )
        for rejected in result.rejected_records:
            quality.append(
                rejection(
                    source_object_uri=input_object.uri,
                    source_event_id=rejected.raw_record.get("settlement_reference"),
                    entity_name="settlements",
                    business_key=rejected.raw_record.get("settlement_reference"),
                    code=_settlement_quality_code(rejected.error_code),
                    message=rejected.error_message,
                    raw_reference=f"row:{rejected.source_row_number}",
                    run_id=run_id,
                    rejected_at=processed_at,
                )
            )
        rows = [
            {
                "partner_id": record.partner_id,
                "settlement_date": record.settlement_date,
                "settlement_reference": record.settlement_reference,
                "partner_transaction_reference": record.partner_transaction_reference,
                "internal_transaction_id": (
                    str(record.internal_transaction_id) if record.internal_transaction_id else None
                ),
                "transaction_timestamp": utc(record.transaction_timestamp),
                "amount": record.amount,
                "currency": record.currency,
                "settlement_status": record.settlement_status,
                "fee_amount": record.fee_amount,
                "net_amount": record.net_amount,
                "source_file_name": file_name,
                "source_checksum": input_object.checksum_sha256,
                "source_row_number": record.source_row_number,
                "ingestion_run_id": input_object.metadata.get("ingestion_run_id"),
                "processing_run_id": run_id,
                "processed_at": processed_at,
            }
            for record in result.accepted_records
        ]
        return rows, quality, result.record_count, partner_id
    finally:
        source.unlink(missing_ok=True)
        work_dir.rmdir()


def _path_partition(object_key: str, name: str) -> str:
    prefix = f"{name}="
    for part in object_key.split("/"):
        if part.startswith(prefix):
            return part.removeprefix(prefix)
    return "UNKNOWN"


def _settlement_quality_code(error_code: str) -> QualityCode:
    if "DECIMAL" in error_code or "AMOUNT" in error_code:
        return QualityCode.INVALID_DECIMAL
    if "TIMESTAMP" in error_code or "DATE" in error_code:
        return QualityCode.INVALID_TIMESTAMP
    if "INTERNAL_TRANSACTION_ID" in error_code:
        return QualityCode.INVALID_REFERENCE
    return QualityCode.INVALID_BRONZE_SCHEMA
