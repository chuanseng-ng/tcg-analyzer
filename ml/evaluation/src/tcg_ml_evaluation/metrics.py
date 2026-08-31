"""§26's defect-detection figures, computed honestly at small support.

Precision, recall and F1 are counting arithmetic; what earns this module a
file is the refusal shape. The corpus this benchmark first runs against has a
two-image test split, so "the class never occurs" is the common case, and a
`0.0` there would read as a measured failure where nothing was measured at
all. Each figure refuses independently — no predictions starves precision, no
truth examples starves recall — and F1 inherits the first refusal it meets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tcg_domain import InsufficientInformation, Uncertain

__all__ = ["ClassMetrics", "ErrorSummary", "class_metrics", "error_summary"]


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
