"""The benchmark report: per split, per axis, per label, counts everywhere.

`evaluate` takes the corpus (from :mod:`tcg_ml_evaluation.manifest`) and the
analyzers' outputs (built by the caller — this package never runs OpenCV) and
returns plain JSON types. Splits are scored separately and never pooled: §27
isolates the test set, and a pooled number would invite reading a tuned-on
figure as held-out performance. Every refusal anywhere in the tree is the
one-key ``{"insufficient_information": reason}`` object, the same shape
`ConditionAssessment.as_record()` uses in `analyses.condition_details`.

**mAP is deliberately absent.** §26 says "mAP where appropriate": a
confidence-ranked detection sweep over the current corpus's handful of
surface markers is fabricated certainty, so it is omitted rather than
computed over nothing. It re-enters when the corpus can support a ranked
sweep — behind a bumped `EVALUATION_VERSION`.

Scoring rules the vocabulary relies on:

* An `unknown` **prediction** is an abstention — counted, never scored as a
  label claim, and it contributes no calibration event.
* An `unknown` **truth** row is unscorable — the annotator could not tell, so
  the region is counted and set aside.
* A surface class the analyzer refused class-level (`not_assessed`) is an
  abstention for that image: its truth rows are neither hits nor misses, and
  the count is the price of the refusal — the busy-face measurement the M7
  notes assign to #188.
"""

from __future__ import annotations

import collections
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from tcg_domain import InsufficientInformation, Uncertain
from tcg_domain.annotation import AnnotationKind, CornerRegion, EdgeRegion
from tcg_domain.condition import (
    ConditionAssessment,
    RegionFinding,
    Representation,
    SurfaceAssessment,
)
from tcg_domain.dataset import DatasetSplit

from tcg_ml_evaluation.calibration import (
    CALIBRATION_BINS,
    Event,
    ReliabilityBin,
    calibration_summary,
)
from tcg_ml_evaluation.manifest import CorpusAnnotation, CorpusMember, EvaluationCorpus
from tcg_ml_evaluation.matching import IOU_THRESHOLD, LabelledBox, match_findings
from tcg_ml_evaluation.metrics import ClassMetrics, class_metrics, error_summary
from tcg_ml_evaluation.truth import (
    current_view,
    is_worked_on,
    newest_centering,
    surface_truth,
)

__all__ = [
    "CENTERING_AGREEMENT_TOLERANCE",
    "EVALUATION_VERSION",
    "ImagePredictions",
    "PredictedCentering",
    "evaluate",
]

#: This harness's own version, recorded beside every metric it produces.
#: Changing any scoring rule or threshold below bumps it.
EVALUATION_VERSION: Final = "condition-evaluation-v0.1.0"

#: A centering claim "agrees" with the annotator when every axis both sides
#: measured lands within this many ratio points — the binary event §25's
#: calibration tools need. Five points is half a typical grading tolerance
#: band; nothing has yet argued for another value.
CENTERING_AGREEMENT_TOLERANCE: Final = 0.05

_UNKNOWN: Final = "unknown"
_CLEAN: Final = "clean"


@dataclass(frozen=True, slots=True)
class PredictedCentering:
    """One side's centering claim, in this package's own primitives.

    Not `ml/centering`'s `SideCentering`: importing that package binds OpenCV,
    and this one deliberately cannot. The caller converts.
    """

    horizontal: Uncertain[float]
    vertical: Uncertain[float]
    confidence: float


@dataclass(frozen=True, slots=True)
class ImagePredictions:
    """What the four analyzers said about one image, as domain shapes."""

    centering: Uncertain[PredictedCentering]
    corners: Uncertain[Mapping[CornerRegion, RegionFinding]]
    edges: Uncertain[Mapping[EdgeRegion, RegionFinding]]
    surface: Uncertain[SurfaceAssessment]


class _AxisTally:
    """One split's running counts for one classification axis."""

    def __init__(self) -> None:
        self.true_positives: collections.Counter[str] = collections.Counter()
        self.false_positives: collections.Counter[str] = collections.Counter()
        self.false_negatives: collections.Counter[str] = collections.Counter()
        self.events: list[Event] = []
        self.predicted_unknown = 0
        self.truth_unknown = 0
        self.axis_refused: collections.Counter[str] = collections.Counter()
        self.abstained: collections.Counter[str] = collections.Counter()
        self.set_aside_original_frame = 0

    def labels(self) -> set[str]:
        return set(self.true_positives) | set(self.false_positives) | set(self.false_negatives)

    def metrics_for(self, label: str) -> ClassMetrics:
        return class_metrics(
            true_positives=self.true_positives[label],
            false_positives=self.false_positives[label],
            false_negatives=self.false_negatives[label],
        )


class _CenteringTally:
    """One split's running centering agreement."""

    def __init__(self) -> None:
        self.horizontal_errors: list[float] = []
        self.vertical_errors: list[float] = []
        self.events: list[Event] = []
        self.refused: collections.Counter[str] = collections.Counter()
        self.not_measured = 0


class _SplitTally:
    def __init__(self) -> None:
        self.images = 0
        self.scored = 0
        self.not_annotated = 0
        self.excluded: collections.Counter[str] = collections.Counter()
        self.corners = _AxisTally()
        self.edges = _AxisTally()
        self.surface = _AxisTally()
        self.centering = _CenteringTally()


def evaluate(
    corpus: EvaluationCorpus,
    *,
    predictions: Mapping[uuid.UUID, ImagePredictions],
    composed: Sequence[Uncertain[ConditionAssessment]] = (),
    excluded: Mapping[uuid.UUID, str] | None = None,
) -> dict[str, object]:
    """Score the analyzers' outputs against the corpus's truth rows.

    Args:
        corpus: The parsed manifest.
        predictions: Per-image analyzer outputs, keyed by training image id.
        composed: The `compose` replays, one per card whose sides both ran —
            the ledger that prices the min-confidence rule.
        excluded: Images the caller could not run the analyzers on, each with
            its reason (no stored artifact, no derivable card frame, …).
    """
    exclusions = dict(excluded or {})
    tallies = {split: _SplitTally() for split in DatasetSplit}

    for member in corpus.members:
        tally = tallies[member.split]
        tally.images += 1
        if member.training_image_id in exclusions:
            tally.excluded[exclusions[member.training_image_id]] += 1
            continue
        prediction = predictions.get(member.training_image_id)
        if prediction is None:
            tally.excluded["no_prediction_supplied"] += 1
            continue
        if not is_worked_on(member):
            # The absence rule must never touch an unexamined image.
            tally.not_annotated += 1
            continue
        tally.scored += 1
        view = current_view(member)
        _score_regions(
            tally.corners,
            prediction.corners,
            view,
            kind=AnnotationKind.CORNER,
            regions=CornerRegion,
        )
        _score_regions(
            tally.edges, prediction.edges, view, kind=AnnotationKind.EDGE, regions=EdgeRegion
        )
        _score_surface(tally.surface, prediction.surface, member)
        _score_centering(tally.centering, prediction.centering, member)

    return {
        "dataset_version": corpus.dataset_version,
        "split_seed": corpus.split_seed,
        "evaluation_version": EVALUATION_VERSION,
        "thresholds": {
            "iou_threshold": IOU_THRESHOLD,
            "centering_agreement_tolerance": CENTERING_AGREEMENT_TOLERANCE,
            "calibration_bins": CALIBRATION_BINS,
        },
        "splits": {str(split): _split_record(tally) for split, tally in tallies.items()},
        "composition": _composition_record(composed),
    }


def _score_regions[R: (CornerRegion, EdgeRegion)](
    tally: _AxisTally,
    answer: Uncertain[Mapping[R, RegionFinding]],
    view: Mapping[tuple[AnnotationKind, str], CorpusAnnotation],
    *,
    kind: AnnotationKind,
    regions: type[R],
) -> None:
    if isinstance(answer, InsufficientInformation):
        tally.axis_refused[answer.reason or "unspecified"] += 1
        return
    for region in regions:
        marker = view.get((kind, region.value))
        truth_label = marker.label if marker is not None else _CLEAN
        if truth_label == _UNKNOWN:
            tally.truth_unknown += 1
            continue
        finding = answer[region]
        predicted_label = str(finding.label)
        if predicted_label == _UNKNOWN:
            tally.predicted_unknown += 1
            continue
        correct = predicted_label == truth_label
        tally.events.append((finding.confidence.value, correct))
        if correct:
            tally.true_positives[truth_label] += 1
        else:
            tally.false_negatives[truth_label] += 1
            tally.false_positives[predicted_label] += 1


def _score_surface(
    tally: _AxisTally, answer: Uncertain[SurfaceAssessment], member: CorpusMember
) -> None:
    if isinstance(answer, InsufficientInformation):
        tally.axis_refused[answer.reason or "unspecified"] += 1
        return

    tally.set_aside_original_frame += len(
        surface_truth(member, representation=Representation.ORIGINAL)
    )
    abstained = {str(label) for label in answer.not_assessed}
    for label in sorted(abstained):
        tally.abstained[label] += 1

    truth_rows: list[LabelledBox] = []
    for row in surface_truth(member, representation=Representation.NORMALIZED):
        if row.label == _UNKNOWN:
            tally.truth_unknown += 1
        elif row.label not in abstained:
            truth_rows.append((row.label, row.bbox))
    predicted_rows: list[LabelledBox] = [
        (str(defect.type), defect.bounding_box) for defect in answer.findings
    ]

    matching = match_findings(predicted=predicted_rows, truth=truth_rows)
    matched_predictions = {p for p, _ in matching.pairs}
    matched_truth = {t for _, t in matching.pairs}
    for index, (label, _) in enumerate(predicted_rows):
        matched = index in matched_predictions
        tally.events.append((answer.findings[index].confidence.value, matched))
        if matched:
            tally.true_positives[label] += 1
        else:
            tally.false_positives[label] += 1
    for index, (label, _) in enumerate(truth_rows):
        if index not in matched_truth:
            tally.false_negatives[label] += 1


def _score_centering(
    tally: _CenteringTally, answer: Uncertain[PredictedCentering], member: CorpusMember
) -> None:
    if isinstance(answer, InsufficientInformation):
        tally.refused[answer.reason or "unspecified"] += 1
        return
    truth = newest_centering(member)
    if truth is None:
        tally.not_measured += 1
        return

    compared: list[float] = []
    for truth_ratio, predicted_ratio, errors in (
        (truth.horizontal, answer.horizontal, tally.horizontal_errors),
        (truth.vertical, answer.vertical, tally.vertical_errors),
    ):
        if truth_ratio is None or isinstance(predicted_ratio, InsufficientInformation):
            continue
        error = abs(predicted_ratio - truth_ratio)
        errors.append(error)
        compared.append(error)
    if compared:
        within = all(error <= CENTERING_AGREEMENT_TOLERANCE for error in compared)
        tally.events.append((answer.confidence, within))


# ---------------------------------------------------------------------------
# Rendering — plain JSON types, every refusal the one-key object
# ---------------------------------------------------------------------------
def _refusal(value: InsufficientInformation) -> dict[str, object]:
    return {"insufficient_information": value.reason}


def _figure(value: Uncertain[float]) -> object:
    return _refusal(value) if isinstance(value, InsufficientInformation) else value


def _metrics_record(metrics: ClassMetrics) -> dict[str, object]:
    return {
        "true_positives": metrics.true_positives,
        "false_positives": metrics.false_positives,
        "false_negatives": metrics.false_negatives,
        "support": metrics.support,
        "precision": _figure(metrics.precision),
        "recall": _figure(metrics.recall),
        "f1": _figure(metrics.f1),
    }


def _bin_record(bin: ReliabilityBin) -> dict[str, object]:
    record: dict[str, object] = {"lower": bin.lower, "upper": bin.upper, "count": bin.count}
    if bin.mean_confidence is not None:
        record["mean_confidence"] = bin.mean_confidence
    if bin.accuracy is not None:
        record["accuracy"] = bin.accuracy
    return record


def _calibration_record(events: Sequence[Event]) -> object:
    summary = calibration_summary(events)
    if isinstance(summary, InsufficientInformation):
        return _refusal(summary)
    return {
        "count": summary.count,
        "brier_score": summary.brier_score,
        "log_loss": summary.log_loss,
        "expected_calibration_error": summary.expected_calibration_error,
        "bins": [_bin_record(bin) for bin in summary.bins],
    }


def _error_record(errors: Sequence[float]) -> object:
    summary = error_summary(errors)
    if isinstance(summary, InsufficientInformation):
        return _refusal(summary)
    return {"mean_error": summary.mean, "max_error": summary.maximum, "count": summary.count}


def _axis_record(tally: _AxisTally, *, surface: bool = False) -> dict[str, object]:
    record: dict[str, object] = {
        "per_label": {
            label: _metrics_record(tally.metrics_for(label)) for label in sorted(tally.labels())
        },
        "calibration": _calibration_record(tally.events),
        "predicted_unknown": tally.predicted_unknown,
        "truth_unknown": tally.truth_unknown,
        "axis_refused": dict(tally.axis_refused),
    }
    if surface:
        record["abstained"] = dict(tally.abstained)
        record["set_aside_original_frame"] = tally.set_aside_original_frame
        del record["predicted_unknown"]  # a surface analyzer abstains class-level instead
    return record


def _split_record(tally: _SplitTally) -> dict[str, object]:
    return {
        "images": tally.images,
        "scored": tally.scored,
        "not_annotated": tally.not_annotated,
        "excluded": dict(tally.excluded),
        "corners": _axis_record(tally.corners),
        "edges": _axis_record(tally.edges),
        "surface": _axis_record(tally.surface, surface=True),
        "centering": {
            "horizontal": _error_record(tally.centering.horizontal_errors),
            "vertical": _error_record(tally.centering.vertical_errors),
            "not_measured": tally.centering.not_measured,
            "refused": dict(tally.centering.refused),
            "calibration": _calibration_record(tally.centering.events),
        },
    }


def _dragged_by_flat_unknown(assessment: ConditionAssessment) -> bool:
    """Whether the min-rule floor is an `unknown` finding's own flat confidence."""
    overall = assessment.confidence
    for axis in (assessment.corners, assessment.edges):
        for answer in axis.values():
            if isinstance(answer, InsufficientInformation):
                continue
            for finding in answer.values():
                if str(finding.label) == _UNKNOWN and finding.confidence == overall:
                    return True
    return False


def _composition_record(
    composed: Sequence[Uncertain[ConditionAssessment]],
) -> dict[str, object]:
    refused: collections.Counter[str] = collections.Counter()
    confidences: list[float] = []
    dragged = 0
    for outcome in composed:
        if isinstance(outcome, InsufficientInformation):
            refused[outcome.reason or "unspecified"] += 1
            continue
        confidences.append(outcome.confidence.value)
        if _dragged_by_flat_unknown(outcome):
            dragged += 1

    overall: object
    if confidences:
        overall = {
            "min": min(confidences),
            "mean": sum(confidences) / len(confidences),
            "max": max(confidences),
            "count": len(confidences),
        }
    else:
        overall = _refusal(InsufficientInformation("no_assessments"))
    return {
        "assessments": len(confidences),
        "refused": dict(refused),
        "dragged_to_flat_unknown_floor": dragged,
        "overall_confidence": overall,
    }
