"""Aggregate orchestration quality gates without copying rejected records."""

from __future__ import annotations

from orchestration.models import QualityClassification, QualityResult


def classify_threshold(
    *,
    rule_name: str,
    observed_value: float,
    warn_threshold: float,
    fail_threshold: float,
    details: dict[str, object] | None = None,
) -> QualityResult:
    if warn_threshold > fail_threshold:
        raise ValueError("warn_threshold must not exceed fail_threshold")
    if observed_value >= fail_threshold:
        classification = QualityClassification.FAIL
    elif observed_value >= warn_threshold:
        classification = QualityClassification.WARN
    else:
        classification = QualityClassification.PASS
    return QualityResult(
        rule_name=rule_name,
        classification=classification,
        observed_value=observed_value,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        details=details or {},
    )


def rejection_rate_result(
    *, records_read: int, records_rejected: int, warn_rate: float, fail_rate: float
) -> QualityResult:
    if records_read < 0 or records_rejected < 0:
        raise ValueError("Record counts cannot be negative")
    denominator = max(records_read, 1)
    rate = records_rejected / denominator
    return classify_threshold(
        rule_name="rejection_rate",
        observed_value=rate,
        warn_threshold=warn_rate,
        fail_threshold=fail_rate,
        details={"records_read": records_read, "records_rejected": records_rejected},
    )


def pipeline_status_from_quality(results: list[QualityResult]) -> str:
    classifications = {result.classification for result in results}
    if QualityClassification.FAIL in classifications:
        return "FAILED"
    if QualityClassification.WARN in classifications:
        return "PARTIAL"
    return "SUCCEEDED"
