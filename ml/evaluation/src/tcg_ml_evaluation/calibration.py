"""§25's probability-quality tools: Brier score, log loss, ECE, reliability bins.

CLAUDE.md names probability calibration as a coverage gap to build in-house
and review carefully; this is that build, applied to what M7's heuristics emit
ahead of M8's mandatory grade-probability calibration. An event is one binary
claim: the analyzer said *p* and was right or wrong; :func:`distribution_summary`
is the multi-class generalisation M8's grade distributions need, and lives
beside it rather than reinterpreting it. What counts as "right"
is the caller's per-axis rule — this module only does the arithmetic, and
refuses to summarise zero events rather than reporting a perfectly calibrated
nothing.

Every analyzer confidence today is a heuristic score, not a calibrated
probability — each analyzer's own docstring says so. These figures are how
that stops being an unmeasured caveat.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tcg_domain import InsufficientInformation, Uncertain

__all__ = [
    "CALIBRATION_BINS",
    "CalibrationSummary",
    "DistributionSummary",
    "ReliabilityBin",
    "calibration_summary",
    "distribution_summary",
]

#: Uniform reliability bins over [0, 1]. Ten is the literature's default; the
#: per-bin counts ride along so a reader can see how thin each one is.
CALIBRATION_BINS: Final = 10

#: Floor for log loss: a confidence of exactly 0 or 1 that turns out wrong is
#: clamped here rather than reported as infinity, and the clamp is part of the
#: figure's definition.
LOG_LOSS_EPSILON: Final = 1e-15

#: One calibration event: the claimed confidence and whether the claim held.
type Event = tuple[float, bool]

#: One multi-class calibration event: the predicted probability of every class
#: in the caller's ladder order, and the index of the class that actually
#: occurred. Deliberately plain numbers — this module knows about probabilities
#: and outcomes, never about grades, and the ladder is the caller's to fix.
type DistributionEvent = tuple[Sequence[float], int]


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    """One slice of the calibration curve, counts included.

    `mean_confidence` and `accuracy` are ``None`` for an empty bin — there is
    nothing to average, and inventing a midpoint would draw a curve through
    data that does not exist.
    """

    lower: float
    upper: float
    count: int
    mean_confidence: float | None
    accuracy: float | None


@dataclass(frozen=True, slots=True)
class CalibrationSummary:
    """§25's figures over one pool of events."""

    brier_score: float
    log_loss: float
    expected_calibration_error: float
    bins: tuple[ReliabilityBin, ...]
    count: int


def calibration_summary(events: Sequence[Event]) -> Uncertain[CalibrationSummary]:
    """Score a pool of (confidence, outcome) events.

    A confidence of 1.0 lands in the last bin rather than overflowing past it.
    """
    if not events:
        return InsufficientInformation("no_events")

    total = len(events)
    brier = sum((confidence - float(outcome)) ** 2 for confidence, outcome in events) / total
    log_loss = (
        -sum(
            math.log(max(confidence if outcome else 1.0 - confidence, LOG_LOSS_EPSILON))
            for confidence, outcome in events
        )
        / total
    )

    binned: list[list[Event]] = [[] for _ in range(CALIBRATION_BINS)]
    for confidence, outcome in events:
        index = min(int(confidence * CALIBRATION_BINS), CALIBRATION_BINS - 1)
        binned[index].append((confidence, outcome))

    bins: list[ReliabilityBin] = []
    expected_calibration_error = 0.0
    for index, members in enumerate(binned):
        lower = index / CALIBRATION_BINS
        upper = (index + 1) / CALIBRATION_BINS
        if not members:
            bins.append(
                ReliabilityBin(
                    lower=lower, upper=upper, count=0, mean_confidence=None, accuracy=None
                )
            )
            continue
        mean_confidence = sum(confidence for confidence, _ in members) / len(members)
        accuracy = sum(1 for _, outcome in members if outcome) / len(members)
        expected_calibration_error += (len(members) / total) * abs(accuracy - mean_confidence)
        bins.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_confidence=mean_confidence,
                accuracy=accuracy,
            )
        )

    return CalibrationSummary(
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=expected_calibration_error,
        bins=tuple(bins),
        count=total,
    )


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    """§25's figures over a pool of multi-class predictions.

    The field names are :class:`CalibrationSummary`'s, so both render through
    the same record shape. The two are not interchangeable: a binary event
    claims one probability, and a distribution claims one per class.
    """

    brier_score: float
    log_loss: float
    expected_calibration_error: float
    bins: tuple[ReliabilityBin, ...]
    count: int


def distribution_summary(events: Sequence[DistributionEvent]) -> Uncertain[DistributionSummary]:
    """Score a pool of (predicted mass, index of what occurred) events.

    Brier and log loss are the multi-class generalisations, computed over the
    caller's fixed class ladder — every event in one pool must use the same
    one, or the figures are not comparable. ECE and the reliability bins come
    from :func:`calibration_summary` over the top-class events, which is the
    binary machinery applied to the model's own most confident claim.

    Nothing here is fitted. §25 requires calibration be *reported*, and
    calibrating against the test split is forbidden outright (§27) — a fitted
    calibrator is a different figure behind a different version.

    Raises:
        ValueError: If an event names an empty ladder or an out-of-range class
            — a caller bug, not a metric.
    """
    if not events:
        return InsufficientInformation("no_events")

    total = len(events)
    brier = 0.0
    log_loss = 0.0
    top_events: list[Event] = []
    for mass, occurred in events:
        if not 0 <= occurred < len(mass):
            raise ValueError(
                f"the class that occurred is index {occurred} of a {len(mass)}-class ladder"
            )
        brier += sum(
            (probability - float(index == occurred)) ** 2 for index, probability in enumerate(mass)
        )
        log_loss -= math.log(max(mass[occurred], LOG_LOSS_EPSILON))
        # Ties break toward the last index, which is the highest grade — the
        # same rule `GradeDistribution.most_likely_grade` uses.
        top = max(range(len(mass)), key=lambda index: (mass[index], index))
        top_events.append((mass[top], top == occurred))

    # Only the ECE and the bins are read from here. The binary Brier and log
    # loss this also computes are over the top-class event, not over the
    # distribution, and reporting them as §25's figures would be the
    # reinterpretation #188 forbids.
    top_label = calibration_summary(top_events)
    if isinstance(top_label, InsufficientInformation):  # pragma: no cover - events is non-empty
        return top_label

    return DistributionSummary(
        brier_score=brier / total,
        log_loss=log_loss / total,
        expected_calibration_error=top_label.expected_calibration_error,
        bins=top_label.bins,
        count=total,
    )
