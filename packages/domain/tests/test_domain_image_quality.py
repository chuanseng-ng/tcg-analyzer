"""The image-quality vocabulary and the report — spec §19, issue #36.

Pure: no OpenCV, no numpy, no image anywhere. What is asserted here is the shape
of an answer, which is the half of the gate that must survive M7 replacing the
implementation.
"""

from __future__ import annotations

import pytest
from tcg_domain.analysis import QualityStatus
from tcg_domain.errors import InvalidQualityReport
from tcg_domain.image_quality import (
    DECIDABLE_WITHOUT_GEOMETRY,
    NEEDS_CARD_GEOMETRY,
    ConditionVerdict,
    QualityCondition,
    QualityFinding,
    QualityReport,
    worst_status,
)

#: Spec §19's list, transcribed here independently of the enum so that the two
#: have to agree. A test that read the enum to check the enum would pass however
#: the list drifted.
SPEC_19_CONDITIONS = (
    "blur",
    "low_resolution",
    "glare",
    "poor_exposure",
    "excessive_darkness",
    "excessive_brightness",
    "severe_perspective_distortion",
    "card_partly_outside_frame",
    "multiple_cards",
    "sleeve_obstruction",
    "insufficient_card_size",
)


def clear(condition: QualityCondition) -> QualityFinding:
    return QualityFinding(condition=condition, verdict=ConditionVerdict.CLEAR)


def undetermined(condition: QualityCondition) -> QualityFinding:
    return QualityFinding(
        condition=condition, verdict=ConditionVerdict.UNDETERMINED, reason="no card located"
    )


def detected(condition: QualityCondition, severity: QualityStatus) -> QualityFinding:
    return QualityFinding(condition=condition, verdict=ConditionVerdict.DETECTED, severity=severity)


def a_report(*overrides: QualityFinding, score: float = 1.0) -> QualityReport:
    """A report where everything is clear, minus whatever the caller replaces."""
    replaced = {finding.condition for finding in overrides}
    findings = [clear(c) for c in QualityCondition if c not in replaced]
    return QualityReport(
        findings=(*findings, *overrides),
        score=score,
        version="image-quality-heuristic-v0.1.0",
    )


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_eleven_conditions_are_exactly_the_ones_the_specification_names() -> None:
    assert tuple(str(condition) for condition in QualityCondition) == SPEC_19_CONDITIONS


def test_every_condition_is_either_decidable_now_or_waiting_on_card_geometry() -> None:
    """No third category, and no overlap — the split is a partition."""
    assert set(QualityCondition) == DECIDABLE_WITHOUT_GEOMETRY | NEEDS_CARD_GEOMETRY
    assert not DECIDABLE_WITHOUT_GEOMETRY & NEEDS_CARD_GEOMETRY


def test_the_five_that_need_geometry_are_the_ones_card_detection_supplies() -> None:
    """#37 flips these on; naming them keeps that hand-off explicit."""
    assert {str(condition) for condition in NEEDS_CARD_GEOMETRY} == {
        "severe_perspective_distortion",
        "card_partly_outside_frame",
        "multiple_cards",
        "sleeve_obstruction",
        "insufficient_card_size",
    }


# ---------------------------------------------------------------------------
# A finding says one thing, or admits it cannot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", [QualityStatus.POOR, QualityStatus.UNUSABLE])
def test_a_detected_condition_carries_how_bad_it_is(severity: QualityStatus) -> None:
    finding = detected(QualityCondition.BLUR, severity)

    assert finding.contribution is severity


@pytest.mark.parametrize("severity", [None, QualityStatus.GOOD, QualityStatus.ACCEPTABLE])
def test_a_detected_condition_must_be_poor_or_unusable(severity: QualityStatus | None) -> None:
    """`good` is not something that was found wrong, and neither is `acceptable`."""
    with pytest.raises(InvalidQualityReport, match="poor or unusable"):
        QualityFinding(
            condition=QualityCondition.BLUR,
            verdict=ConditionVerdict.DETECTED,
            severity=severity,
        )


def test_only_a_detected_condition_carries_a_severity() -> None:
    with pytest.raises(InvalidQualityReport, match="severity"):
        QualityFinding(
            condition=QualityCondition.BLUR,
            verdict=ConditionVerdict.CLEAR,
            severity=QualityStatus.POOR,
        )


def test_an_undetermined_condition_must_say_why() -> None:
    """ "Undetermined" with no explanation is a gap wearing an answer's clothes."""
    with pytest.raises(InvalidQualityReport, match="must say why"):
        QualityFinding(
            condition=QualityCondition.MULTIPLE_CARDS, verdict=ConditionVerdict.UNDETERMINED
        )


def test_an_unchecked_condition_is_neither_a_pass_nor_a_failure() -> None:
    """It must not look clean, and it must not refuse an image on its own."""
    assert undetermined(QualityCondition.MULTIPLE_CARDS).contribution is QualityStatus.ACCEPTABLE
    assert clear(QualityCondition.BLUR).contribution is QualityStatus.GOOD


def test_a_measurement_must_be_a_finite_number() -> None:
    with pytest.raises(InvalidQualityReport, match="finite"):
        QualityFinding(
            condition=QualityCondition.BLUR,
            verdict=ConditionVerdict.CLEAR,
            measurement=float("nan"),
        )


# ---------------------------------------------------------------------------
# A report answers for all eleven
# ---------------------------------------------------------------------------


def test_a_report_must_carry_every_condition() -> None:
    """The acceptance criterion, made structural rather than remembered."""
    with pytest.raises(InvalidQualityReport, match="missing"):
        QualityReport(findings=(clear(QualityCondition.BLUR),), score=1.0, version="v0.1.0-test")


def test_a_report_may_not_answer_the_same_condition_twice() -> None:
    with pytest.raises(InvalidQualityReport, match="repeated"):
        QualityReport(
            findings=(*(clear(c) for c in QualityCondition), clear(QualityCondition.BLUR)),
            score=1.0,
            version="v0.1.0-test",
        )


def test_a_score_outside_the_unit_interval_is_refused() -> None:
    with pytest.raises(InvalidQualityReport, match=r"\[0, 1\]"):
        a_report(score=1.5)


def test_a_version_may_not_be_a_pointer_to_whatever_is_current() -> None:
    """The same refusal the card-database version makes, for the same reason."""
    with pytest.raises(InvalidQualityReport, match="fixed gate"):
        QualityReport(
            findings=tuple(clear(c) for c in QualityCondition),
            score=1.0,
            version="image-quality-latest",
        )


def test_everything_clear_is_good() -> None:
    assert a_report().status is QualityStatus.GOOD


def test_anything_unchecked_makes_it_acceptable_rather_than_good() -> None:
    """Why M2 never returns `good`: five conditions wait on #37's card geometry."""
    report = a_report(undetermined(QualityCondition.MULTIPLE_CARDS))

    assert report.status is QualityStatus.ACCEPTABLE


def test_the_worst_finding_is_the_verdict() -> None:
    report = a_report(
        detected(QualityCondition.BLUR, QualityStatus.POOR),
        detected(QualityCondition.GLARE, QualityStatus.UNUSABLE),
        undetermined(QualityCondition.MULTIPLE_CARDS),
    )

    assert report.status is QualityStatus.UNUSABLE


def test_a_status_cannot_disagree_with_the_findings() -> None:
    """It is derived, not stored: there is no field to set inconsistently."""
    assert not hasattr(QualityReport, "__dataclass_fields__") or (
        "status" not in QualityReport.__dataclass_fields__
    )


def test_the_thresholds_are_copied_rather_than_referenced() -> None:
    """A record has to still describe the run that wrote it."""
    thresholds = {"blur_variance_poor": 120.0}
    report = QualityReport(
        findings=tuple(clear(c) for c in QualityCondition),
        score=1.0,
        version="v0.1.0-test",
        thresholds=thresholds,
    )
    thresholds["blur_variance_poor"] = 1.0

    assert report.thresholds["blur_variance_poor"] == 120.0


# ---------------------------------------------------------------------------
# The persisted form
# ---------------------------------------------------------------------------


def test_the_record_carries_the_gate_that_produced_it() -> None:
    record = a_report().as_record()

    assert record["version"] == "image-quality-heuristic-v0.1.0"
    assert len(record["findings"]) == len(QualityCondition)  # type: ignore[arg-type]


def test_the_record_is_plain_json_types() -> None:
    """It goes into a JSONB column; nothing may need a serializer to know this package."""
    import json

    json.dumps(a_report(detected(QualityCondition.BLUR, QualityStatus.POOR)).as_record())


def test_a_clear_finding_records_no_severity() -> None:
    record = a_report().as_record()
    findings: list[dict[str, object]] = record["findings"]  # type: ignore[assignment]

    assert all("severity" not in finding for finding in findings)


# ---------------------------------------------------------------------------
# Folding several images into one analysis
# ---------------------------------------------------------------------------


def test_the_analysis_takes_the_worse_of_its_photographs() -> None:
    """One unusable side is enough to stop it — spec §19."""
    assert worst_status([QualityStatus.GOOD, QualityStatus.UNUSABLE]) is QualityStatus.UNUSABLE


def test_an_analysis_with_no_photographs_folds_to_good() -> None:
    """Not a verdict anything acts on: `run` requires both sides to exist first."""
    assert worst_status([]) is QualityStatus.GOOD
