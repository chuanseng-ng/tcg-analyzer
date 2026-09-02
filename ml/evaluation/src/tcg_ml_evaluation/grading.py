"""Score a predicted grade distribution against the grade a company issued.

Spec §26's grade-prediction block and §25's probability quality, per split and
per grading company, beside #188's condition metrics and sharing its rules:
splits are never pooled, counts sit beside every figure, every refusal is the
one-key ``{"insufficient_information": reason}`` object, and an abstention is
counted rather than scored.

**This lands before the predictors it judges** (#223 to #225), which is #188's
decomposition rule: a benchmark written afterwards invites confirmation bias,
and a predictor PR that cannot quote a number promises one instead.

Two rules are worth reading before changing anything here, because getting
either wrong makes a model look better for free.

*±1 is a step on the company's own ladder.* PSA and TAG issue no 9.5 and BGS
does, so a BGS 9.5 is one step from 10 and a BGS 9 is two, while a PSA 9 is one
— see :func:`ladder_distance`. Arithmetic on the grade *value* would silently
hand BGS an easier target.

*A prediction is read two ways, and they are different views on purpose.*
Accuracy reads the distribution's own terms, through
:attr:`~tcg_domain.distribution.GradeDistribution.most_likely_grade`.
Probability quality reads the **ladder projection**: a bucket's mass is spread
uniformly over the scale points it collapses, so the class set is the company's
full ladder and two predictors of one company are comparable. The two views can
name different grades when a bucket is involved; that is the honest price of
coarseness rather than a bug.

Nothing here fits anything. §25 requires calibration be **reported**, ADR 0011
decision 3 says so in the words M8 stands behind, and calibrating against the
test split is forbidden outright (§27). A fitted calibrator — temperature,
isotonic — is a different figure behind a different version.
"""

from __future__ import annotations

import collections
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from tcg_domain import InsufficientInformation, Uncertain
from tcg_domain.dataset import DatasetSplit
from tcg_domain.distribution import GradeDistribution
from tcg_domain.grade import Grade, GradeBound
from tcg_grading_companies.errors import UnsupportedGrade
from tcg_grading_companies.port import GradePrediction
from tcg_grading_companies.scale import GradeScale

from tcg_ml_evaluation.calibration import (
    CALIBRATION_BINS,
    DistributionEvent,
    ReliabilityBin,
    distribution_summary,
)
from tcg_ml_evaluation.metrics import AccuracyRate, accuracy_rate, wilson_lower_bound

__all__ = [
    "GRADE_EVALUATION_VERSION",
    "WILSON_Z_95",
    "WITHIN_ONE_TARGET",
    "GradeSubject",
    "IssuedGrade",
    "covered_points",
    "evaluate_grades",
    "is_exact_hit",
    "ladder_distance",
]

#: This harness's own version, recorded beside every metric it produces and
#: part of its experiment record's filename. Deliberately *not*
#: `report.EVALUATION_VERSION`: that one names the condition harness, scores a
#: different input from a different runner, and has a committed record whose
#: numbers this issue did not change. Changing any scoring rule or constant
#: below bumps this one and leaves that one alone.
GRADE_EVALUATION_VERSION: Final = "grade-evaluation-v0.1.0"

#: Spec §27's target: at least this share of predictions within ±1 of the
#: issued grade. ADR 0011 decision 2 — moving it is a new ADR, not an edit.
WITHIN_ONE_TARGET: Final = 0.80

#: Two-sided 95%, the confidence level §27's claim is made at. ADR 0011
#: decision 2 again: the target is claimed only when the Wilson score *lower
#: bound* clears :data:`WITHIN_ONE_TARGET`, per company, on the test split.
WILSON_Z_95: Final = 1.959963984540054


@dataclass(frozen=True, slots=True)
class IssuedGrade:
    """What one company actually issued for one physical copy — the target.

    Args:
        company: The company's lowercase slug.
        grade: The point on that company's scale, or ``None`` when the slab
            carries a designation instead. #165's column is nullable for
            exactly this reason: PSA issues "Authentic" *in place of* a number,
            and there is then no point to score.
        designation: The designation, when there is one.

    Raises:
        ValueError: If `grade` is a bucket. A slab prints one point; a
            collapsed tail is a model's output, never a company's.
    """

    company: str
    grade: Grade | None
    designation: str | None = None

    def __post_init__(self) -> None:
        if self.grade is not None and self.grade.is_bucket:
            raise ValueError(f"a company issues one point, not the bucket {self.grade}")


@dataclass(frozen=True, slots=True)
class GradeSubject:
    """One card that was predicted, and what happened to it.

    Predictions and outcomes are both the **caller's** — this package reads a
    manifest and never a database (ADR 0009), and the manifest does not carry a
    target yet (#220). When it does, the runner fills `outcomes` from the
    member rather than from anywhere new, and this shape does not move.

    Args:
        subject_id: The physical copy the prediction and the outcome are both
            about. A grade belongs to the copy, not to one photograph of it.
        split: Which partition the subject belongs to. Splits are scored
            separately and never pooled (§27).
        predictions: Per company slug, what the model said — or an
            :class:`~tcg_domain.confidence.InsufficientInformation` when it
            abstained. A raised
            :class:`~tcg_grading_companies.errors.GradePredictionUnavailable`
            is the caller's to catch and pass here as a refusal, the way
            `predict_or_exclude` handles an analyzer that raised.
        outcomes: Per company slug, the grade that company issued.
    """

    subject_id: uuid.UUID
    split: DatasetSplit
    predictions: Mapping[str, Uncertain[GradePrediction]]
    outcomes: Mapping[str, IssuedGrade]


# ---------------------------------------------------------------------------
# The ladder, and the bucket rule
# ---------------------------------------------------------------------------
def _covers(bucket: Grade, point: Grade) -> bool:
    """Whether `point` is one of the exact grades `bucket` collapses.

    The rule spec §24's ``7_or_lower`` key needs, restated for scoring. It is
    deliberately *not* imported from `tcg_economic_engine.expectation`, which
    holds the same six lines for pricing: "grading is separate from economics"
    is CLAUDE.md's master architectural rule, and a helper shared across that
    boundary is precisely the dependency it forbids.

    ponytail: six lines duplicated across an invariant boundary. If a third
    caller appears, hoist it to `Grade.covers` in the domain — which both sides
    already depend on — rather than making either side import the other.
    """
    if point.is_bucket:
        return False
    if bucket.bound is GradeBound.OR_LOWER:
        return point.value <= bucket.value
    return point.value >= bucket.value


def covered_points(scale: GradeScale, grade: Grade) -> tuple[Grade, ...]:
    """The points of `scale` that `grade` names, ascending.

    An exact grade names itself. A bucket names every scale point in its tail,
    so PSA's ``7_or_lower`` names thirteen of that company's eighteen. A grade
    the company cannot issue names nothing — callers reach
    :meth:`GradeScale.validate` first, which turns that into a refusal rather
    than a silent zero.
    """
    if not grade.is_bucket:
        return (grade,) if grade in scale.grades else ()
    return tuple(point for point in scale.ordered if _covers(grade, point))


def ladder_distance(scale: GradeScale, predicted: Grade, issued: Grade) -> int:
    """How many steps of `scale` separate `predicted` from `issued`.

    Steps, never grade points: BGS's ladder has a 9.5 and PSA's does not, so
    "within ±1" is a different span on each and reading it as ±1.0 on the value
    would flatter BGS for free (ADR 0011 decision 2).

    A bucket sits at its nearest covered point, so a bucket that covers the
    issued grade is zero steps away.

    Raises:
        ValueError: If either grade is off the scale — the caller validates the
            distribution and the outcome before scoring either.
    """
    position = {grade: index for index, grade in enumerate(scale.ordered)}
    if issued not in position:
        raise ValueError(f"{scale.company} does not issue grade {issued}")
    points = covered_points(scale, predicted)
    if not points:
        raise ValueError(f"{scale.company} does not issue grade {predicted}")
    return min(abs(position[point] - position[issued]) for point in points)


def is_exact_hit(scale: GradeScale, predicted: Grade, issued: Grade) -> bool:
    """Whether `predicted` names the issued grade and nothing else.

    A bucket is not an exact call: ``7_or_lower`` against a truth of 6 is
    right about the tail and silent about the point. It qualifies only when the
    company's scale leaves it exactly one point to collapse, which falls out of
    the same line rather than needing a second rule.
    """
    return covered_points(scale, predicted) == (issued,)


def _projection(scale: GradeScale, distribution: GradeDistribution) -> list[float]:
    """The distribution's mass over `scale`'s full ladder, in ladder order.

    A bucket's mass is spread **uniformly** over the points it collapses. That
    is a scoring convention rather than a claim about the model: it keeps the
    class set identical for every predictor of one company — which is what
    makes two of them comparable — and it prices coarseness, because a wide
    bucket dilutes onto the point that actually occurred.
    """
    ladder = scale.ordered
    position = {grade: index for index, grade in enumerate(ladder)}
    mass = [0.0] * len(ladder)
    for term, probability in distribution.items():
        points = covered_points(scale, term)
        share = probability / len(points)
        for point in points:
            mass[position[point]] += share
    return mass


# ---------------------------------------------------------------------------
# Tallying
# ---------------------------------------------------------------------------
class _CompanyTally:
    """One split's running counts for one grading company."""

    def __init__(self) -> None:
        self.subjects = 0
        self.scored = 0
        self.exact_hits = 0
        self.within_one_hits = 0
        self.excluded: collections.Counter[str] = collections.Counter()
        self.abstained: collections.Counter[str] = collections.Counter()
        self.unscorable: collections.Counter[str] = collections.Counter()
        self.confusion: dict[str, collections.Counter[str]] = {}
        self.events: list[DistributionEvent] = []
        self.model_versions: set[str] = set()


def evaluate_grades(
    subjects: Sequence[GradeSubject],
    *,
    dataset_version: str,
    split_seed: int,
    scales: Mapping[str, GradeScale],
) -> dict[str, object]:
    """Score per-company grade predictions against the grades issued.

    Args:
        subjects: One entry per physical copy that was predicted.
        dataset_version: The version the subjects were drawn from — the record
            names both its inputs, as #188's does.
        split_seed: That version's split seed.
        scales: The ladder to score each company against, by slug. A parameter
            rather than a reach into `ADAPTERS`, so a fourth company costs a
            caller one entry and this module nothing.

    Returns:
        Plain JSON types: every split, every company, counts beside every
        figure, and a refusal wherever there was nothing to measure.

    Raises:
        UnsupportedGrade: If a prediction or an issued grade names a grade the
            company cannot issue. Scoring one silently would flatter the model.
        ValueError: If a subject names a company with no scale.
    """
    companies = sorted(scales)
    tallies = {split: {company: _CompanyTally() for company in companies} for split in DatasetSplit}

    for subject in subjects:
        unknown = (set(subject.predictions) | set(subject.outcomes)) - set(scales)
        if unknown:
            raise ValueError(f"no grade scale supplied for {', '.join(sorted(unknown))}")
        for company in companies:
            _score_subject(tallies[subject.split][company], subject, company, scales[company])

    return {
        "dataset_version": dataset_version,
        "split_seed": split_seed,
        "grade_evaluation_version": GRADE_EVALUATION_VERSION,
        "thresholds": {
            "within_one_target": WITHIN_ONE_TARGET,
            "wilson_z": WILSON_Z_95,
            "calibration_bins": CALIBRATION_BINS,
        },
        "splits": {
            str(split): {company: _company_record(tally) for company, tally in by_company.items()}
            for split, by_company in tallies.items()
        },
    }


def _score_subject(
    tally: _CompanyTally, subject: GradeSubject, company: str, scale: GradeScale
) -> None:
    tally.subjects += 1
    prediction = subject.predictions.get(company)
    if prediction is None:
        tally.excluded["no_prediction_supplied"] += 1
        return
    if isinstance(prediction, InsufficientInformation):
        # Counted, never scored: a model that declined to answer is a ledger
        # entry, not a miss (#188's abstention rule).
        tally.abstained[prediction.reason or "unspecified"] += 1
        return

    tally.model_versions.add(prediction.model_version)
    scale.validate(prediction.grade_probability)

    outcome = subject.outcomes.get(company)
    if outcome is None:
        tally.unscorable["no_issued_grade"] += 1
        return
    if outcome.grade is None:
        tally.unscorable["designation_without_grade"] += 1
        return
    if not scale.supports(outcome.grade):
        raise UnsupportedGrade(
            f"{company} does not issue grade {outcome.grade}; its scale is "
            f"{', '.join(str(item) for item in scale.ordered)}"
        )

    issued = outcome.grade
    predicted = prediction.grade_probability.most_likely_grade
    tally.scored += 1
    if is_exact_hit(scale, predicted, issued):
        tally.exact_hits += 1
    if ladder_distance(scale, predicted, issued) <= 1:
        tally.within_one_hits += 1
    tally.confusion.setdefault(str(issued), collections.Counter())[str(predicted)] += 1

    mass = _projection(scale, prediction.grade_probability)
    tally.events.append((mass, scale.ordered.index(issued)))


# ---------------------------------------------------------------------------
# Rendering — plain JSON types, every refusal the one-key object
# ---------------------------------------------------------------------------
def _refusal(value: InsufficientInformation) -> dict[str, object]:
    return {"insufficient_information": value.reason}


def _bin_record(bin: ReliabilityBin) -> dict[str, object]:
    record: dict[str, object] = {"lower": bin.lower, "upper": bin.upper, "count": bin.count}
    if bin.mean_confidence is not None:
        record["mean_confidence"] = bin.mean_confidence
    if bin.accuracy is not None:
        record["accuracy"] = bin.accuracy
    return record


def _accuracy_record(rate: Uncertain[AccuracyRate]) -> object:
    if isinstance(rate, InsufficientInformation):
        return _refusal(rate)
    return {"hits": rate.hits, "count": rate.count, "rate": rate.rate}


def _within_one_record(rate: Uncertain[AccuracyRate]) -> object:
    """§27's figure, with the bound the claim is actually made against.

    The rate alone is not the claim: ADR 0011 decision 2 makes §27 conditional
    on the Wilson lower bound clearing the target, so both travel together and
    a reader never sees the rate without the interval around it.
    """
    if isinstance(rate, InsufficientInformation):
        return _refusal(rate)
    bound = wilson_lower_bound(hits=rate.hits, count=rate.count, z=WILSON_Z_95)
    return {
        "hits": rate.hits,
        "count": rate.count,
        "rate": rate.rate,
        "wilson_lower_bound": bound,
        "target": WITHIN_ONE_TARGET,
        "meets_target": bound >= WITHIN_ONE_TARGET,
    }


def _probability_record(events: Sequence[DistributionEvent]) -> object:
    summary = distribution_summary(events)
    if isinstance(summary, InsufficientInformation):
        return _refusal(summary)
    return {
        "count": summary.count,
        "brier_score": summary.brier_score,
        "log_loss": summary.log_loss,
        "expected_calibration_error": summary.expected_calibration_error,
        "bins": [_bin_record(bin) for bin in summary.bins],
    }


def _company_record(tally: _CompanyTally) -> dict[str, object]:
    return {
        "subjects": tally.subjects,
        "scored": tally.scored,
        "model_versions": sorted(tally.model_versions),
        "excluded": dict(tally.excluded),
        "abstained": dict(tally.abstained),
        "unscorable": dict(tally.unscorable),
        "exact_accuracy": _accuracy_record(
            accuracy_rate(hits=tally.exact_hits, count=tally.scored)
        ),
        "within_one_accuracy": _within_one_record(
            accuracy_rate(hits=tally.within_one_hits, count=tally.scored)
        ),
        "confusion": {
            issued: dict(predicted) for issued, predicted in sorted(tally.confusion.items())
        },
        "probability_quality": _probability_record(tally.events),
    }
