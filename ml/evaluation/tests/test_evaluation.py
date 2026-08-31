"""The benchmark's arithmetic, against hand-calculated fixtures.

Every figure here was worked out by hand before the implementation existed —
the repository's rule for formulas (CLAUDE.md's economic-engine precedent,
applied to §26's metrics and §25's calibration tools). A metric over zero
samples answers `InsufficientInformation`, never a number: with a two-image
test split, fabricated certainty is the failure mode this package exists to
refuse.
"""

from __future__ import annotations

import pytest
from tcg_domain import InsufficientInformation
from tcg_domain.condition import BoundingBox
from tcg_ml_evaluation.calibration import (
    CALIBRATION_BINS,
    CalibrationSummary,
    calibration_summary,
)
from tcg_ml_evaluation.matching import IOU_THRESHOLD, iou, match_findings
from tcg_ml_evaluation.metrics import class_metrics, error_summary


# ---------------------------------------------------------------------------
# Precision / recall / F1 — §26's defect-detection metrics
# ---------------------------------------------------------------------------
def test_precision_recall_and_f1_against_a_hand_calculated_fixture() -> None:
    """tp=3, fp=1, fn=2: precision 3/4, recall 3/5, F1 = 2pr/(p+r) = 2/3."""
    result = class_metrics(true_positives=3, false_positives=1, false_negatives=2)

    assert result.precision == pytest.approx(0.75)
    assert result.recall == pytest.approx(0.6)
    assert result.f1 == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))
    assert result.support == 5
    assert result.true_positives == 3
    assert result.false_positives == 1
    assert result.false_negatives == 2


def test_a_class_with_no_predictions_refuses_precision_rather_than_dividing() -> None:
    result = class_metrics(true_positives=0, false_positives=0, false_negatives=2)

    assert result.precision == InsufficientInformation("no_predictions")
    assert result.recall == pytest.approx(0.0)
    assert result.f1 == InsufficientInformation("no_predictions")


def test_a_class_with_no_truth_examples_refuses_recall_rather_than_dividing() -> None:
    """The two-image test split's everyday case: the class never occurs."""
    result = class_metrics(true_positives=0, false_positives=2, false_negatives=0)

    assert result.precision == pytest.approx(0.0)
    assert result.recall == InsufficientInformation("no_examples")
    assert result.f1 == InsufficientInformation("no_examples")


def test_all_zeros_is_a_refusal_on_every_figure_and_never_a_perfect_score() -> None:
    result = class_metrics(true_positives=0, false_positives=0, false_negatives=0)

    assert result.precision == InsufficientInformation("no_predictions")
    assert result.recall == InsufficientInformation("no_examples")
    assert result.f1 == InsufficientInformation("no_examples")
    assert result.support == 0


def test_found_everything_but_also_hallucinated_gives_f1_between_the_two() -> None:
    """tp=2, fp=2, fn=0: precision 1/2, recall 1, F1 = 2/3."""
    result = class_metrics(true_positives=2, false_positives=2, false_negatives=0)

    assert result.f1 == pytest.approx(2 / 3)


def test_a_negative_count_is_a_programming_error_not_a_metric() -> None:
    with pytest.raises(ValueError):
        class_metrics(true_positives=-1, false_positives=0, false_negatives=0)


# ---------------------------------------------------------------------------
# Error summaries — §21-adjacent centering agreement, in ratio points
# ---------------------------------------------------------------------------
def test_error_summary_reports_mean_max_and_count() -> None:
    summary = error_summary((0.02, 0.06))

    assert summary.mean == pytest.approx(0.04)
    assert summary.maximum == pytest.approx(0.06)
    assert summary.count == 2


def test_an_empty_error_summary_is_a_refusal_never_a_zero() -> None:
    assert error_summary(()) == InsufficientInformation("no_samples")


# ---------------------------------------------------------------------------
# IoU matching — predicted findings against annotated markers
# ---------------------------------------------------------------------------
def test_iou_of_a_hand_calculated_overlap() -> None:
    """Two 0.2-square boxes overlapping in a 0.1 square: 0.01 / 0.07 = 1/7."""
    a = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)
    b = BoundingBox(x=0.2, y=0.2, width=0.2, height=0.2)

    assert iou(a, b) == pytest.approx(1 / 7)


def test_iou_of_disjoint_boxes_is_zero() -> None:
    a = BoundingBox(x=0.0, y=0.0, width=0.1, height=0.1)
    b = BoundingBox(x=0.5, y=0.5, width=0.1, height=0.1)

    assert iou(a, b) == 0.0


def test_identical_boxes_match_and_the_pair_is_reported() -> None:
    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)

    matching = match_findings(
        predicted=(("stain", box),),
        truth=(("stain", box),),
    )

    assert matching.pairs == ((0, 0),)


def test_boxes_below_the_iou_threshold_do_not_match() -> None:
    """1/7 sits below the 0.5 threshold, so both sides go unmatched."""
    a = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)
    b = BoundingBox(x=0.2, y=0.2, width=0.2, height=0.2)
    assert iou(a, b) < IOU_THRESHOLD

    matching = match_findings(predicted=(("stain", a),), truth=(("stain", b),))

    assert matching.pairs == ()


def test_labels_must_agree_even_when_boxes_are_identical() -> None:
    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)

    matching = match_findings(predicted=(("scuff", box),), truth=(("stain", box),))

    assert matching.pairs == ()


def test_a_boxless_truth_marker_matches_by_label() -> None:
    """Coordinates need an artifact; the marker itself does not (#160)."""
    box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)

    matching = match_findings(predicted=(("stain", box),), truth=(("stain", None),))

    assert matching.pairs == ((0, 0),)


def test_each_truth_marker_matches_at_most_once_and_the_best_overlap_wins() -> None:
    truth_box = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)
    close = BoundingBox(x=0.12, y=0.1, width=0.2, height=0.2)
    exact = BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2)

    matching = match_findings(
        predicted=(("stain", close), ("stain", exact)),
        truth=(("stain", truth_box),),
    )

    assert matching.pairs == ((1, 0),)


# ---------------------------------------------------------------------------
# Calibration — §25's Brier score, log loss, ECE and reliability bins
# ---------------------------------------------------------------------------
def test_brier_score_against_a_hand_calculated_fixture() -> None:
    """((0.8-1)² + (0.5-0)² + (1-1)²) / 3 = 0.29 / 3."""
    summary = calibration_summary(((0.8, True), (0.5, False), (1.0, True)))

    assert isinstance(summary, CalibrationSummary)
    assert summary.brier_score == pytest.approx(0.29 / 3)
    assert summary.count == 3


def test_log_loss_against_a_hand_calculated_fixture() -> None:
    """-(ln 0.8 + ln 0.8) / 2 for one hit at 0.8 and one miss at 0.2."""
    summary = calibration_summary(((0.8, True), (0.2, False)))

    assert isinstance(summary, CalibrationSummary)
    assert summary.log_loss == pytest.approx(0.22314355131420976)


def test_log_loss_clamps_a_certain_claim_rather_than_reporting_infinity() -> None:
    summary = calibration_summary(((1.0, False),))

    assert isinstance(summary, CalibrationSummary)
    assert summary.log_loss > 30.0  # -ln(epsilon), finite and enormous


def test_expected_calibration_error_against_a_hand_calculated_fixture() -> None:
    """Bin [0.9, 1.0]: 2 events, mean 0.95, accuracy 0.5 → (2/3)·0.45.

    Bin [0.5, 0.6): 1 event, mean 0.55, accuracy 1.0 → (1/3)·0.45. ECE = 0.45.
    """
    summary = calibration_summary(((0.95, True), (0.95, False), (0.55, True)))

    assert isinstance(summary, CalibrationSummary)
    assert summary.expected_calibration_error == pytest.approx(0.45)


def test_reliability_bins_carry_counts_and_a_full_confidence_lands_in_the_last() -> None:
    summary = calibration_summary(((1.0, True),))

    assert isinstance(summary, CalibrationSummary)
    assert len(summary.bins) == CALIBRATION_BINS
    assert [bin.count for bin in summary.bins[:-1]] == [0] * (CALIBRATION_BINS - 1)
    last = summary.bins[-1]
    assert last.count == 1
    assert last.mean_confidence == pytest.approx(1.0)
    assert last.accuracy == pytest.approx(1.0)
    assert (last.lower, last.upper) == (pytest.approx(0.9), pytest.approx(1.0))


def test_an_empty_bin_reports_no_mean_and_no_accuracy() -> None:
    summary = calibration_summary(((1.0, True),))

    assert isinstance(summary, CalibrationSummary)
    empty = summary.bins[0]
    assert empty.mean_confidence is None
    assert empty.accuracy is None


def test_zero_events_is_a_refusal_never_a_perfectly_calibrated_nothing() -> None:
    assert calibration_summary(()) == InsufficientInformation("no_events")
