"""PII-safe quality rejection construction."""

from __future__ import annotations

from datetime import datetime

from processing.silver.models import QualityCode, QualityRejection


def rejection(
    *,
    source_object_uri: str,
    source_event_id: str | None,
    entity_name: str,
    business_key: str | None,
    code: QualityCode | str,
    message: str,
    raw_reference: str,
    run_id: str,
    rejected_at: datetime,
) -> QualityRejection:
    """Create bounded quality evidence without embedding a business payload."""

    return QualityRejection(
        source_object_uri=source_object_uri,
        source_event_id=source_event_id,
        entity_name=entity_name,
        business_key=business_key,
        error_code=code.value if isinstance(code, QualityCode) else code,
        error_message=message[:1000],
        raw_reference=raw_reference[:512],
        processing_run_id=run_id,
        rejected_at=rejected_at,
    )
