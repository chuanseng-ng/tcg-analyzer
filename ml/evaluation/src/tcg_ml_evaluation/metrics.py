"""§26's counting figures, computed honestly at small support.

Precision, recall and F1 are counting arithmetic; what earns this module a
file is the refusal shape. The corpus this benchmark first runs against has a
two-image test split, so "the class never occurs" is the common case, and a
`0.0` there would read as a measured failure where nothing was measured at
all. Each figure refuses independently — no predictions starves precision, no
truth examples starves recall — and F1 inherits the first refusal it meets.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tcg_domain import InsufficientInformation, Uncertain

__all__ = [
    "AccuracyRate",
    "ClassMetrics",
    "ErrorSummary",
    "accuracy_rate",
    "class_metrics",
    "error_summary",
    "wilson_lower_bound",
]


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    """One label's detection figures, with the counts that produced them.

    The counts always ride along (spec §2.7 by way of #188's issue text:
    "report counts beside every metric"), so a reader can see that a perfect
    precision was earned over two samples rather than two hundred.
    """

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: Uncertain[float]
    recall: Uncertain[float]
    f1: Uncertain[float]

    @property
    def support(self) -> int:
        """How many truth examples the class had: tp + fn."""
        return self.true_positives + self.false_negatives


@dataclass(frozen=True, slots=True)
class ErrorSummary:
    """Absolute errors summarised — centering agreement, in ratio points."""

    mean: float
    maximum: float
    count: int


@dataclass(frozen=True, slots=True)
class AccuracyRate:
    """One accuracy figure and the counts behind it — §26's grade metrics.

    The counts ride along for the same reason `ClassMetrics`' do: a perfect
    rate over four samples is not the same claim as a perfect rate over four
    hundred, and §27's target is read against the interval rather than the
    rate — see :func:`wilson_lower_bound`.
    """

    hits: int
    count: int
    rate: float


def class_metrics(
    *, true_positives: int, false_positives: int, false_negatives: int
) -> ClassMetrics:
    """Precision, recall and F1 for one label.

    Raises:
        ValueError: If any count is negative — a matching bug, not a metric.
    """
    if min(true_positives, false_positives, false_negatives) < 0:
        raise ValueError(
            f"counts must be non-negative, got tp={true_positives} "
            f"fp={false_positives} fn={false_negatives}"
        )

    predicted = true_positives + false_positives
    actual = true_positives + false_negatives
    precision: Uncertain[float] = (
        true_positives / predicted if predicted else InsufficientInformation("no_predictions")
    )
    recall: Uncertain[float] = (
        true_positives / actual if actual else InsufficientInformation("no_examples")
    )

    f1: Uncertain[float]
    if isinstance(recall, InsufficientInformation):
        f1 = recall
    elif isinstance(precision, InsufficientInformation):
        f1 = precision
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    return ClassMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def error_summary(errors: Sequence[float]) -> Uncertain[ErrorSummary]:
    """Mean and maximum absolute error, refusing an empty sample outright."""
    if not errors:
        return InsufficientInformation("no_samples")
    return ErrorSummary(
        mean=sum(errors) / len(errors),
        maximum=max(errors),
        count=len(errors),
    )


def accuracy_rate(*, hits: int, count: int) -> Uncertain[AccuracyRate]:
    """One accuracy figure with the counts that produced it.

    Raises:
        ValueError: If the counts are negative or claim more hits than
            attempts — a tallying bug, not a metric.
    """
    if min(hits, count) < 0 or hits > count:
        raise ValueError(f"expected 0 <= hits <= count, got hits={hits} count={count}")
    if count == 0:
        return InsufficientInformation("no_scored_predictions")
    return AccuracyRate(hits=hits, count=count, rate=hits / count)


def wilson_lower_bound(*, hits: int, count: int, z: float) -> float:
    """The lower end of the Wilson score interval for `hits` of `count`.

    ADR 0011 decision 2 makes spec §27's claim conditional on this figure
    rather than on the observed rate: the interval self-scales, so a perfect
    4/4 reports 0.5101 and even a flawless record cannot clear 0.80 below
    n = 16. The normal approximation would report 1.0 for the same 4/4, which
    is exactly the fabricated certainty the product exists to refuse.

    `z` is the caller's — the harness passes ADR 0011's two-sided 95% value,
    and moving it is a new ADR rather than an argument.

    Raises:
        ValueError: If the counts are negative or claim more hits than
            attempts, or if `count` is zero — an undefined interval is a
            refusal the caller must have made already.
    """
    if count <= 0 or min(hits, count) < 0 or hits > count:
        raise ValueError(f"expected 0 <= hits <= count and count > 0, got {hits}/{count}")
    observed = hits / count
    z_squared = z * z
    centre = observed + z_squared / (2 * count)
    half_width = z * math.sqrt(observed * (1 - observed) / count + z_squared / (4 * count * count))
    return (centre - half_width) / (1 + z_squared / count)
