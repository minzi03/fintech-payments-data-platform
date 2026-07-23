from __future__ import annotations

import pytest

from orchestration.models import QualityClassification
from orchestration.quality import (
    classify_threshold,
    pipeline_status_from_quality,
    rejection_rate_result,
)


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        (0.0, QualityClassification.PASS),
        (0.01, QualityClassification.WARN),
        (0.05, QualityClassification.FAIL),
    ],
)
def test_quality_threshold_classification(observed: float, expected: QualityClassification) -> None:
    result = classify_threshold(
        rule_name="rejection_rate",
        observed_value=observed,
        warn_threshold=0.01,
        fail_threshold=0.05,
    )

    assert result.classification is expected


def test_rejection_rate_and_partial_pipeline_semantics() -> None:
    result = rejection_rate_result(
        records_read=100, records_rejected=2, warn_rate=0.01, fail_rate=0.05
    )

    assert result.observed_value == 0.02
    assert pipeline_status_from_quality([result]) == "PARTIAL"
