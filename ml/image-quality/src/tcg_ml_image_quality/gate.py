"""The image-quality gate — spec §19, issue #36.

Bytes in, a :class:`~tcg_domain.image_quality.QualityReport` out. No database,
no object storage, no HTTP — `tcg_api.analysis.image_validation` is the model,
and for the same reason: everything security- or product-critical about this
module can then be asserted by a test that needs no infrastructure at all.

**Six of spec §19's eleven are decided here.** The other five —
perspective distortion, card partly outside frame, multiple cards, sleeve
obstruction, insufficient card size — all reduce to "where is the card", which
is #37's card boundary detection; its own scope says it "feeds back into the
quality gate". They are reported :data:`ConditionVerdict.UNDETERMINED` with a
reason, which is what the acceptance criterion asks for and what spec §2.7
requires: a gate that answered "no sleeve" without having looked for one would
be exactly the confidently-wrong output the specification forbids.

**#37 adds a `geometry` keyword argument here.** It is deliberately not
declared yet: the shape of a detected quadrilateral — corner order, coordinate
space, whether a detection confidence rides along — is #37's decision to make,
and a parameter typed against a class nobody has defined would pre-empt it. The
argument is keyword-only when it arrives, so no caller of :func:`assess`
changes.

**This is a heuristic, and M7 replaces it with a model.** What must not change
when it does is this signature and :class:`QualityReport` — the thresholds and
the measurements are this implementation's, and are recorded on every image so
the model can be compared against the baseline that actually ran.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from tcg_domain.analysis import QualityStatus
from tcg_domain.image_quality import (
    NEEDS_CARD_GEOMETRY,
    ConditionVerdict,
    QualityCondition,
    QualityFinding,
    QualityReport,
)

from tcg_ml_image_quality.thresholds import (
    DEFAULT_THRESHOLDS,
    IMAGE_QUALITY_VERSION,
    QualityThresholds,
)

__all__ = ["UnreadableImage", "assess"]

#: OpenCV's own array type. Preferred over a narrower `NDArray[np.uint8]`
#: because that is not what its functions are annotated to return, and
#: correcting each call with a cast would be describing the library rather
#: than this module.
_Gray = MatLike

#: `(unusable, poor, ideal)` for one measurement — see `QualityThresholds`.
_Limits = tuple[float, float, float]

#: Why the geometric five cannot be answered yet. One sentence, stored on every
#: image, naming the thing that is missing rather than the issue number — the
#: record outlives the issue tracker.
_NO_GEOMETRY: Final = "the card has not been located in the photograph yet"


class UnreadableImage(ValueError):
    """The bytes could not be decoded.

    Not something a user reaches: `POST /analyses/{id}/images` has already
    decoded and re-encoded these bytes before storing them, so a failure here
    means the stored object is not what the row says it is. It is a job failure,
    and the runner's retry and dead-letter path is where it belongs.
    """


def assess(
    data: bytes,
    *,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Judge one photograph against spec §19's eleven conditions.

    Args:
        data: The stored image, JPEG or PNG.
        thresholds: Where each condition crosses into poor and then unusable.
            Recorded in the report, so the verdict explains itself later.

    Returns:
        A report carrying all eleven conditions, a `[0, 1]` score and the
        version and thresholds that produced them.

    Raises:
        UnreadableImage: If the bytes do not decode.
    """
    colour = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if colour is None:
        raise UnreadableImage("The stored image could not be decoded.")

    original_height, original_width = colour.shape[:2]
    gray = _working_gray(colour, long_edge=thresholds.work_long_edge)

    measurements = _measure(
        gray,
        short_edge=min(original_width, original_height),
        glare_level=thresholds.glare_level,
    )
    findings = [_finding(condition, measurements, thresholds) for condition in _DECIDED]
    findings.extend(
        QualityFinding(
            condition=condition,
            verdict=ConditionVerdict.UNDETERMINED,
            reason=_NO_GEOMETRY,
        )
        for condition in sorted(NEEDS_CARD_GEOMETRY)
    )

    return QualityReport(
        findings=tuple(sorted(findings, key=lambda f: list(QualityCondition).index(f.condition))),
        score=_score(measurements, thresholds),
        version=IMAGE_QUALITY_VERSION,
        thresholds=thresholds.as_record(),
    )


# ---------------------------------------------------------------------------
# Measuring.
#
# One pass over the pixels producing every number, so that adding a seventh
# condition does not add a seventh decode. The numbers are separated from the
# thresholds deliberately: what was measured is a fact about the photograph and
# is persisted, where what it means is policy and can be recalibrated.
# ---------------------------------------------------------------------------


def _working_gray(colour: _Gray, *, long_edge: int) -> _Gray:
    """Grayscale, scaled so the long edge is at most `long_edge`.

    Scaled because none of the measurements below are scale-invariant: an
    identical photograph at 8 and at 48 megapixels would otherwise get two
    different blur verdicts, and the second is the one a modern phone takes.
    `INTER_AREA` because it is the correct filter for shrinking — a nearest or
    linear downscale aliases fine texture into what looks like sharpness.

    Never enlarged. Upscaling a small photograph would invent detail and hide
    the very thing `low_resolution` exists to catch.
    """
    gray: _Gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    scale = long_edge / max(height, width)
    if scale >= 1.0:
        return gray
    return cv2.resize(
        gray,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _measure(gray: _Gray, *, short_edge: int, glare_level: int) -> dict[QualityCondition, float]:
    """Every decidable condition's measurement, in one pass."""
    low, high = (float(value) for value in np.percentile(gray, (5, 95)))
    return {
        # The *original* short edge, not the working copy's: this is the one
        # measurement that is about the file rather than about the picture.
        QualityCondition.LOW_RESOLUTION: float(short_edge),
        QualityCondition.BLUR: float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        QualityCondition.EXCESSIVE_DARKNESS: float(gray.mean()),
        QualityCondition.EXCESSIVE_BRIGHTNESS: float(gray.mean()),
        QualityCondition.POOR_EXPOSURE: high - low,
        QualityCondition.GLARE: _largest_saturated_fraction(gray, level=glare_level),
    }


def _largest_saturated_fraction(gray: _Gray, *, level: int) -> float:
    """The biggest connected blown-out region, as a fraction of the frame.

    The largest *component*, not the total saturated area, and the distinction
    is the whole heuristic. A card with a white border and a white background
    saturates a large fraction of the frame and has no glare at all; a single
    specular reflection off the holo layer is one blob and ruins the surface
    signal underneath it.
    """
    mask = (gray >= level).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0
    # Row 0 is the background component; CC_STAT_AREA is column 4.
    largest = int(stats[1:, cv2.CC_STAT_AREA].max())
    return largest / float(gray.size)


# ---------------------------------------------------------------------------
# Judging.
# ---------------------------------------------------------------------------

#: Which measurement each condition reads, and which direction is better. The
#: order is spec §19's, so a report reads the way the specification does.
_DECIDED: Final = (
    QualityCondition.BLUR,
    QualityCondition.LOW_RESOLUTION,
    QualityCondition.GLARE,
    QualityCondition.POOR_EXPOSURE,
    QualityCondition.EXCESSIVE_DARKNESS,
    QualityCondition.EXCESSIVE_BRIGHTNESS,
)


def _limits(condition: QualityCondition, thresholds: QualityThresholds) -> _Limits:
    """The `(unusable, poor, ideal)` triple for one condition.

    Looked up by the name `thresholds.limits()` uses rather than by field, so
    the two cannot drift: a condition this gate decides but that has no triple is
    a `KeyError` on the first photograph, not a silent pass.
    """
    triples = {name: (unusable, poor, ideal) for name, unusable, poor, ideal in thresholds.limits()}
    return triples[str(condition)]


def _severity(value: float, limits: _Limits) -> QualityStatus:
    """Which side of the two consequential lines `value` falls on.

    The direction comes from the triple's own ordering — see
    :class:`QualityThresholds`. Normalising to "larger is better" first means one
    comparison rather than one per direction.
    """
    scaled, unusable, poor, _ideal = _ascending(value, limits)
    if scaled <= unusable:
        return QualityStatus.UNUSABLE
    if scaled <= poor:
        return QualityStatus.POOR
    return QualityStatus.GOOD


def _finding(
    condition: QualityCondition,
    measurements: dict[QualityCondition, float],
    thresholds: QualityThresholds,
) -> QualityFinding:
    value = measurements[condition]
    severity = _severity(value, _limits(condition, thresholds))
    if severity is QualityStatus.GOOD:
        return QualityFinding(
            condition=condition, verdict=ConditionVerdict.CLEAR, measurement=value
        )
    return QualityFinding(
        condition=condition,
        verdict=ConditionVerdict.DETECTED,
        severity=severity,
        measurement=value,
    )


def _margin(value: float, limits: _Limits) -> float:
    """How good `value` is, on a `[0, 1]` scale.

    Piecewise linear through the three points: the unusable threshold is 0, the
    poor threshold is 0.5, and the ideal is 1. Putting `poor` exactly at the
    halfway mark is what makes `score < 0.5` and "something was detected" the
    same fact, and the ideal is what makes 1 reachable — a derived upper knot
    would put it out of reach for a measurement like glare, whose best possible
    value sits closer to `poor` than `poor` does to `unusable`.
    """
    scaled, unusable, poor, ideal = _ascending(value, limits)
    if scaled <= unusable:
        return 0.0
    if scaled >= ideal:
        return 1.0
    if scaled <= poor:
        return 0.5 * (scaled - unusable) / (poor - unusable)
    return 0.5 + 0.5 * (scaled - poor) / (ideal - poor)


def _ascending(value: float, limits: _Limits) -> tuple[float, float, float, float]:
    """`(value, unusable, poor, ideal)` with larger always meaning better.

    Every measurement is judged with one set of comparisons; the ones where a
    smaller number is better are negated on the way in. Two mirrored branches
    would be two places for an inequality to be written backwards.
    """
    unusable, poor, ideal = limits
    if ideal < poor:
        return (-value, -unusable, -poor, -ideal)
    return (value, unusable, poor, ideal)


def _score(measurements: dict[QualityCondition, float], thresholds: QualityThresholds) -> float:
    """One `[0, 1]` verdict for the photograph, 1 being best.

    The **minimum** over the decided conditions, so the weakest link governs —
    the same rule that makes the status the worst finding. That is what keeps
    the two consistent: `score < 0.5` exactly when something was detected, which
    is worth more than an average that lets five clean measurements hide a
    ruinous sixth. Undetermined conditions contribute nothing; a gate that
    scored an unchecked condition would be inventing a measurement.
    """
    return min(
        _margin(measurements[condition], _limits(condition, thresholds)) for condition in _DECIDED
    )
