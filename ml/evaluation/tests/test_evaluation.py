"""The benchmark's arithmetic, against hand-calculated fixtures.

Every figure here was worked out by hand before the implementation existed —
the repository's rule for formulas (CLAUDE.md's economic-engine precedent,
applied to §26's metrics and §25's calibration tools). A metric over zero
samples answers `InsufficientInformation`, never a number: with a two-image
test split, fabricated certainty is the failure mode this package exists to
refuse.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import uuid
from datetime import UTC, datetime

import pytest
from tcg_domain import Confidence, ImageSide, InsufficientInformation
from tcg_domain.annotation import (
    AnnotationKind,
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_domain.condition import (
    BoundingBox,
    ConditionAssessment,
    Defect,
    RegionFinding,
    Representation,
    SurfaceAssessment,
)
from tcg_domain.dataset import DatasetSplit
from tcg_ml_evaluation.calibration import (
    CALIBRATION_BINS,
    CalibrationSummary,
    calibration_summary,
)
from tcg_ml_evaluation.manifest import (
    CorpusAnnotation,
    CorpusCentering,
    CorpusMember,
    EvaluationCorpus,
    load_manifest,
)
from tcg_ml_evaluation.matching import IOU_THRESHOLD, iou, match_findings
from tcg_ml_evaluation.metrics import class_metrics, error_summary
from tcg_ml_evaluation.report import (
    CENTERING_AGREEMENT_TOLERANCE,
    EVALUATION_VERSION,
    ImagePredictions,
    PredictedCentering,
    evaluate,
)
from tcg_ml_evaluation.truth import current_view, is_worked_on, newest_centering, surface_truth


def _manifest_payload() -> dict[str, object]:
    """A two-member manifest in the committed file's own shape."""
    return {
        "dataset_version": "pokemon-condition-v0.1.0",
        "split_seed": 1,
        "members": [
            {
                "training_image_id": "00000000-0000-0000-0000-0000000000aa",
                "sha256": "aa" * 32,
                "split": "test",
                "side": "front",
                "source": "first_party",
                "acquisition_method": "photographed_before_submission",
                "original_uri": "training/aa.png",
                "annotations": [
                    {
                        "id": "00000000-0000-0000-0000-00000000a001",
                        "kind": "corner",
                        "region": "top_left",
                        "label": "whitening",
                        "severity": "minor",
                        "confidence": 0.9,
                        "representation": "normalized",
                        "created_at": "2026-08-30T09:00:00+00:00",
                        "bbox": {"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1},
                    }
                ],
                "centering": [
                    {
                        "id": "00000000-0000-0000-0000-00000000c001",
                        "horizontal": 0.55,
                        "confidence": 0.9,
                        "created_at": "2026-08-30T09:00:00+00:00",
                    }
                ],
            },
            {
                "training_image_id": "00000000-0000-0000-0000-0000000000ab",
                "sha256": "ab" * 32,
                "split": "train",
                "side": "back",
                "source": "first_party",
                "acquisition_method": "photographed_before_submission",
                "original_uri": "training/ab.png",
                "annotations": [],
                "centering": [],
            },
        ],
    }


def _annotation(
    *,
    kind: AnnotationKind = AnnotationKind.CORNER,
    region: str | None = "top_left",
    label: str = "whitening",
    representation: Representation = Representation.NORMALIZED,
    minute: int = 0,
    identifier: str = "00000000-0000-0000-0000-00000000a001",
) -> CorpusAnnotation:
    return CorpusAnnotation(
        id=uuid.UUID(identifier),
        kind=kind,
        region=region,
        label=label,
        severity="minor",
        confidence=0.9,
        bbox=None,
        representation=representation,
        created_at=datetime(2026, 8, 30, 9, minute, tzinfo=UTC),
    )


def _corpus_member(
    *,
    annotations: tuple[CorpusAnnotation, ...] = (),
    centering: tuple[CorpusCentering, ...] = (),
) -> CorpusMember:
    return CorpusMember(
        training_image_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        sha256="aa" * 32,
        split=DatasetSplit.TEST,
        side="front",
        source="first_party",
        acquisition_method="photographed_before_submission",
        original_uri="training/aa.png",
        annotations=annotations,
        centering=centering,
    )


# ---------------------------------------------------------------------------
# The manifest is the seam — ml/* reads the file, never the database
# ---------------------------------------------------------------------------
def test_a_manifest_round_trips_into_typed_members() -> None:
    corpus = load_manifest(json.dumps(_manifest_payload()))

    assert isinstance(corpus, EvaluationCorpus)
    assert corpus.dataset_version == "pokemon-condition-v0.1.0"
    assert corpus.split_seed == 1
    annotated, bare = corpus.members
    assert annotated.split is DatasetSplit.TEST
    (marker,) = annotated.annotations
    assert marker.kind is AnnotationKind.CORNER
    assert marker.representation is Representation.NORMALIZED
    assert marker.bbox == BoundingBox(x=0.0, y=0.0, width=0.1, height=0.1)
    (measurement,) = annotated.centering
    assert measurement.horizontal == 0.55
    assert measurement.vertical is None
    assert bare.annotations == ()


def test_a_manifest_with_no_members_is_refused() -> None:
    payload = _manifest_payload()
    payload["members"] = []

    with pytest.raises(ValueError, match="no members"):
        load_manifest(json.dumps(payload))


def test_a_manifest_rendered_before_the_annotation_fields_is_refused() -> None:
    """An old file silently read as 'no annotations' would score everything clean."""
    payload = _manifest_payload()
    for member in payload["members"]:  # type: ignore[union-attr]
        del member["annotations"]
        del member["centering"]

    with pytest.raises(ValueError, match="regenerate"):
        load_manifest(json.dumps(payload))


# ---------------------------------------------------------------------------
# The truth protocol: newest row wins, absence is clean only when worked on
# ---------------------------------------------------------------------------
def test_an_image_with_any_row_is_worked_on_and_one_with_none_is_not() -> None:
    assert not is_worked_on(_corpus_member())
    assert is_worked_on(_corpus_member(annotations=(_annotation(),)))
    assert is_worked_on(
        _corpus_member(
            centering=(
                CorpusCentering(
                    id=uuid.UUID("00000000-0000-0000-0000-00000000c001"),
                    horizontal=0.55,
                    vertical=None,
                    confidence=0.9,
                    created_at=datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
                ),
            )
        )
    )


def test_the_newest_row_per_region_is_the_current_view() -> None:
    """A correction is a new row; the older one must not double-count."""
    older = _annotation(label="whitening", minute=0)
    newer = _annotation(label="clean", minute=1, identifier="00000000-0000-0000-0000-00000000a002")

    view = current_view(_corpus_member(annotations=(newer, older)))

    assert view[(AnnotationKind.CORNER, "top_left")].label == "clean"


def test_surface_rows_are_never_collapsed_into_a_current_view() -> None:
    """A surface has as many defects as it has rows; two stains are two truths."""
    first = _annotation(kind=AnnotationKind.SURFACE, region=None, label="stain", minute=0)
    second = _annotation(
        kind=AnnotationKind.SURFACE,
        region=None,
        label="stain",
        minute=1,
        identifier="00000000-0000-0000-0000-00000000a002",
    )
    member = _corpus_member(annotations=(first, second))

    assert current_view(member) == {}
    assert surface_truth(member, representation=Representation.NORMALIZED) == (first, second)


def test_surface_truth_filters_by_declared_frame_and_never_converts() -> None:
    """#175: the frames relate by a projective warp; a reader filters, never projects."""
    artifact_row = _annotation(kind=AnnotationKind.SURFACE, region=None, label="stain")
    original_row = _annotation(
        kind=AnnotationKind.SURFACE,
        region=None,
        label="scratch",
        representation=Representation.ORIGINAL,
        identifier="00000000-0000-0000-0000-00000000a002",
    )
    member = _corpus_member(annotations=(artifact_row, original_row))

    assert surface_truth(member, representation=Representation.NORMALIZED) == (artifact_row,)
    assert surface_truth(member, representation=Representation.ORIGINAL) == (original_row,)


def test_the_newest_centering_measurement_is_the_current_reading() -> None:
    older = CorpusCentering(
        id=uuid.UUID("00000000-0000-0000-0000-00000000c001"),
        horizontal=0.5,
        vertical=0.5,
        confidence=0.9,
        created_at=datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
    )
    newer = CorpusCentering(
        id=uuid.UUID("00000000-0000-0000-0000-00000000c002"),
        horizontal=0.6,
        vertical=None,
        confidence=0.9,
        created_at=datetime(2026, 8, 30, 9, 1, tzinfo=UTC),
    )

    assert newest_centering(_corpus_member(centering=(newer, older))) == newer
    assert newest_centering(_corpus_member()) is None


# ---------------------------------------------------------------------------
# The report: per split, per axis, per label, counts beside everything
# ---------------------------------------------------------------------------
def _clean_finding(confidence: float = 0.95) -> RegionFinding:
    return RegionFinding(label=CornerLabel.CLEAN, confidence=Confidence.of(confidence))


def _corner_predictions(
    top_left: RegionFinding | None = None,
) -> dict[CornerRegion, RegionFinding]:
    findings = {region: _clean_finding() for region in CornerRegion}
    if top_left is not None:
        findings[CornerRegion.TOP_LEFT] = top_left
    return findings


def _predictions(
    *,
    corners: object = None,
    surface: object = None,
    centering: object = None,
) -> ImagePredictions:
    return ImagePredictions(
        centering=(
            centering
            if centering is not None
            else InsufficientInformation("no_card_frame_for_side")
        ),
        corners=(
            corners if corners is not None else InsufficientInformation("no_card_frame_for_side")
        ),
        edges=InsufficientInformation("no_card_frame_for_side"),
        surface=(
            surface if surface is not None else InsufficientInformation("no_card_frame_for_side")
        ),
    )


def _corpus(*members: CorpusMember) -> EvaluationCorpus:
    return EvaluationCorpus(
        dataset_version="pokemon-condition-v0.1.0", split_seed=1, members=members
    )


def test_the_report_names_its_versions_and_its_thresholds() -> None:
    member = _corpus_member(annotations=(_annotation(),))

    report = evaluate(_corpus(member), predictions={member.training_image_id: _predictions()})

    assert report["dataset_version"] == "pokemon-condition-v0.1.0"
    assert report["split_seed"] == 1
    assert report["evaluation_version"] == EVALUATION_VERSION
    assert report["thresholds"] == {
        "iou_threshold": IOU_THRESHOLD,
        "centering_agreement_tolerance": CENTERING_AGREEMENT_TOLERANCE,
        "calibration_bins": CALIBRATION_BINS,
    }
    assert set(report["splits"]) == {"train", "validation", "test"}


def test_corner_regions_score_against_the_current_view_with_absence_as_clean() -> None:
    """One whitening truth + three absent-thus-clean; the analyzer agrees on all four.

    whitening: tp=1. clean: tp=3. Calibration: four correct events at
    0.9, 0.95, 0.95, 0.95 so Brier = (0.01 + 3 * 0.0025) / 4 = 0.004375.
    """
    member = _corpus_member(annotations=(_annotation(),))
    predicted = _predictions(
        corners=_corner_predictions(
            RegionFinding(
                label=CornerLabel.WHITENING,
                confidence=Confidence.of(0.9),
                severity=DefectSeverity.MINOR,
            )
        )
    )

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    corners = report["splits"]["test"]["corners"]
    assert corners["per_label"]["whitening"]["true_positives"] == 1
    assert corners["per_label"]["whitening"]["precision"] == 1.0
    assert corners["per_label"]["whitening"]["recall"] == 1.0
    assert corners["per_label"]["clean"]["true_positives"] == 3
    assert corners["calibration"]["count"] == 4
    assert corners["calibration"]["brier_score"] == pytest.approx(0.004375)


def test_a_disagreement_counts_against_both_labels_and_into_calibration() -> None:
    """Truth whitening, predicted clean: fn for whitening, fp for clean, a miss at 0.95."""
    member = _corpus_member(annotations=(_annotation(),))
    predicted = _predictions(corners=_corner_predictions())

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    corners = report["splits"]["test"]["corners"]
    assert corners["per_label"]["whitening"]["false_negatives"] == 1
    assert corners["per_label"]["whitening"]["recall"] == 0.0
    assert corners["per_label"]["clean"]["false_positives"] == 1
    assert corners["per_label"]["clean"]["true_positives"] == 3
    assert corners["calibration"]["count"] == 4


def test_an_unknown_prediction_abstains_and_an_unknown_truth_is_unscorable() -> None:
    member = _corpus_member(
        annotations=(
            _annotation(),
            _annotation(
                region="top_right",
                label="unknown",
                identifier="00000000-0000-0000-0000-00000000a002",
            ),
        )
    )
    predicted = _predictions(
        corners=_corner_predictions(
            RegionFinding(label=CornerLabel.UNKNOWN, confidence=Confidence.of(0.5))
        )
    )

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    corners = report["splits"]["test"]["corners"]
    assert corners["predicted_unknown"] == 1
    assert corners["truth_unknown"] == 1
    # top_left abstained, top_right unscorable: only the two bottom corners score.
    assert corners["per_label"]["clean"]["true_positives"] == 2
    assert corners["calibration"]["count"] == 2


def test_an_image_with_no_rows_is_never_scored_by_the_absence_rule() -> None:
    member = _corpus_member()
    predicted = _predictions(corners=_corner_predictions())

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    split = report["splits"]["test"]
    assert split["not_annotated"] == 1
    assert split["corners"]["per_label"] == {}


def test_a_refused_axis_is_a_counted_reason_never_a_zero() -> None:
    member = _corpus_member(annotations=(_annotation(),))

    report = evaluate(_corpus(member), predictions={member.training_image_id: _predictions()})

    corners = report["splits"]["test"]["corners"]
    assert corners["axis_refused"] == {"no_card_frame_for_side": 1}
    assert corners["calibration"] == {"insufficient_information": "no_events"}


def test_surface_findings_match_by_iou_and_an_abstained_class_is_not_a_miss() -> None:
    """A matched stain is a tp; an unmatched scuff a fp; an abstained class neither."""
    box = BoundingBox(x=0.1, y=0.1, width=0.1, height=0.1)
    member = _corpus_member(
        annotations=(
            _annotation(kind=AnnotationKind.SURFACE, region=None, label="stain"),
            _annotation(
                kind=AnnotationKind.SURFACE,
                region=None,
                label="print_line",
                identifier="00000000-0000-0000-0000-00000000a002",
            ),
        )
    )
    predicted = _predictions(
        surface=SurfaceAssessment(
            findings=(
                Defect(
                    type=SurfaceLabel.STAIN,
                    confidence=Confidence.of(0.9),
                    severity=DefectSeverity.MINOR,
                    side=ImageSide.FRONT,
                    representation=Representation.NORMALIZED,
                    bounding_box=box,
                ),
                Defect(
                    type=SurfaceLabel.SCUFF,
                    confidence=Confidence.of(0.6),
                    severity=DefectSeverity.MINOR,
                    side=ImageSide.FRONT,
                    representation=Representation.NORMALIZED,
                    bounding_box=BoundingBox(x=0.5, y=0.5, width=0.1, height=0.1),
                ),
            ),
            not_assessed={SurfaceLabel.PRINT_LINE: InsufficientInformation("below_sampling_limit")},
        )
    )

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    surface = report["splits"]["test"]["surface"]
    assert surface["per_label"]["stain"]["true_positives"] == 1
    assert surface["per_label"]["scuff"]["false_positives"] == 1
    assert surface["per_label"]["scuff"]["recall"] == {"insufficient_information": "no_examples"}
    assert surface["abstained"]["print_line"] == 1
    assert "print_line" not in surface["per_label"]
    assert surface["calibration"]["count"] == 2


def test_original_frame_truth_is_set_aside_never_scored_against_the_artifact() -> None:
    member = _corpus_member(
        annotations=(
            _annotation(
                kind=AnnotationKind.SURFACE,
                region=None,
                label="scratch",
                representation=Representation.ORIGINAL,
            ),
        )
    )
    predicted = _predictions(surface=SurfaceAssessment(findings=()))

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    surface = report["splits"]["test"]["surface"]
    assert surface["set_aside_original_frame"] == 1
    assert "scratch" not in surface["per_label"]


def test_centering_error_is_reported_in_ratio_points_with_a_tolerance_event() -> None:
    member = _corpus_member(
        centering=(
            CorpusCentering(
                id=uuid.UUID("00000000-0000-0000-0000-00000000c001"),
                horizontal=0.55,
                vertical=0.41,
                confidence=0.9,
                created_at=datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
            ),
        )
    )
    predicted = _predictions(
        centering=PredictedCentering(
            horizontal=0.57,
            vertical=InsufficientInformation("no_vertical_frame"),
            confidence=0.8,
        )
    )

    report = evaluate(_corpus(member), predictions={member.training_image_id: predicted})

    centering = report["splits"]["test"]["centering"]
    assert centering["horizontal"]["mean_error"] == pytest.approx(0.02)
    assert centering["horizontal"]["count"] == 1
    assert centering["vertical"] == {"insufficient_information": "no_samples"}
    assert centering["calibration"]["count"] == 1
    assert centering["calibration"]["brier_score"] == pytest.approx((1 - 0.8) ** 2)


def test_splits_are_scored_separately_and_never_pooled() -> None:
    test_member = _corpus_member(annotations=(_annotation(),))
    train_member = CorpusMember(
        training_image_id=uuid.UUID("00000000-0000-0000-0000-0000000000ab"),
        sha256="ab" * 32,
        split=DatasetSplit.TRAIN,
        side="back",
        source="first_party",
        acquisition_method="photographed_before_submission",
        original_uri="training/ab.png",
        annotations=(_annotation(identifier="00000000-0000-0000-0000-00000000a003"),),
        centering=(),
    )
    predictions = {
        test_member.training_image_id: _predictions(corners=_corner_predictions()),
        train_member.training_image_id: _predictions(corners=_corner_predictions()),
    }

    report = evaluate(_corpus(test_member, train_member), predictions=predictions)

    assert report["splits"]["test"]["images"] == 1
    assert report["splits"]["train"]["images"] == 1
    assert report["splits"]["test"]["corners"]["per_label"]["clean"]["true_positives"] == 3
    assert report["splits"]["train"]["corners"]["per_label"]["clean"]["true_positives"] == 3


def test_an_excluded_image_is_a_counted_reason() -> None:
    member = _corpus_member(annotations=(_annotation(),))

    report = evaluate(
        _corpus(member),
        predictions={},
        excluded={member.training_image_id: "no_normalized_artifact"},
    )

    split = report["splits"]["test"]
    assert split["excluded"] == {"no_normalized_artifact": 1}
    assert split["scored"] == 0


def test_an_original_frame_prediction_is_refused_rather_than_scored() -> None:
    """The scorer compares artifact-frame boxes; handing it an original-frame
    defect is a programming error, not a scorable finding (#175's rule,
    applied to the prediction side as well as the truth side)."""
    member = _corpus_member(
        annotations=(_annotation(kind=AnnotationKind.SURFACE, region=None, label="stain"),)
    )
    predicted = _predictions(
        surface=SurfaceAssessment(
            findings=(
                Defect(
                    type=SurfaceLabel.STAIN,
                    confidence=Confidence.of(0.9),
                    severity=DefectSeverity.MINOR,
                    side=ImageSide.FRONT,
                    representation=Representation.ORIGINAL,
                ),
            )
        )
    )

    with pytest.raises(ValueError, match="original"):
        evaluate(_corpus(member), predictions={member.training_image_id: predicted})


def test_the_composition_ledger_prices_the_flat_unknown_drag() -> None:
    """#188 owns pricing the min rule: a flat-0.5 unknown drags the whole assessment."""
    dragged = ConditionAssessment(
        centering=InsufficientInformation("no_frame"),
        corners={
            ImageSide.FRONT: _corner_predictions(
                RegionFinding(label=CornerLabel.UNKNOWN, confidence=Confidence.of(0.5))
            ),
            ImageSide.BACK: _corner_predictions(),
        },
        edges={
            ImageSide.FRONT: {
                region: RegionFinding(label=EdgeLabel.CLEAN, confidence=Confidence.of(0.95))
                for region in EdgeRegion
            },
            ImageSide.BACK: {
                region: RegionFinding(label=EdgeLabel.CLEAN, confidence=Confidence.of(0.95))
                for region in EdgeRegion
            },
        },
        surface={
            ImageSide.FRONT: InsufficientInformation("busy"),
            ImageSide.BACK: InsufficientInformation("busy"),
        },
        manufacturing_defects=InsufficientInformation("feeding_classes_unassessed"),
        eye_appeal=InsufficientInformation("eye_appeal_not_measured_in_v1"),
        confidence=Confidence.of(0.5),
    )
    member = _corpus_member(annotations=(_annotation(),))

    report = evaluate(
        _corpus(member),
        predictions={member.training_image_id: _predictions()},
        composed=(dragged, InsufficientInformation("no_axis_measured")),
    )

    ledger = report["composition"]
    assert ledger["assessments"] == 1
    assert ledger["refused"] == {"no_axis_measured": 1}
    assert ledger["dragged_to_flat_unknown_floor"] == 1
    assert ledger["overall_confidence"]["mean"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Purity: the scorer binds to no CV stack, no database and no object store
# ---------------------------------------------------------------------------
def test_scoring_pulls_in_neither_opencv_nor_a_database_nor_a_store() -> None:
    """ADR 0009: `ml/*` reads a manifest, not the database — and this package
    scores domain shapes, so it must not acquire the analyzers' CV stack
    either. The same fresh-interpreter probe as
    `packages/shared/tests/test_storage_purity.py`, for the same reason.
    """
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_evaluation  # noqa: F401
        prefixes = ("cv2", "numpy", "sqlalchemy", "asyncpg", "boto3", "botocore")
        print(json.dumps(sorted(
            name for name in sys.modules if name.startswith(prefixes)
        )))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


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
