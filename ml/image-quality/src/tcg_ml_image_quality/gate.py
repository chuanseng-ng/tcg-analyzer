"""The image-quality gate — spec §19, issue #36.

Bytes in, a :class:`~tcg_domain.image_quality.QualityReport` out. No database,
no object storage, no HTTP — `tcg_api.analysis.image_validation` is the model,
and for the same reason: everything security- or product-critical about this
module can then be asserted by a test that needs no infrastructure at all.

**Five of spec §19's eleven are decided from the frame alone.** The other six
need to know where the card is, which is #37's card boundary detection; its own
scope says it "feeds back into the quality gate". They are decided here too, but
only when the `geometry` argument carries a
:class:`~tcg_domain.card_geometry.CardGeometry`.

Five of those six — perspective distortion, card partly outside frame, multiple
cards, sleeve obstruction, insufficient card size — are questions *about* the
boundary. **Glare is the sixth and is not**: it is a measurement of pixels like
the five above it, but of the card's pixels. Measured over the whole frame it is
diluted by whatever the card is lying on, and #190 measured that dilution as
blindness — a corpus a person says carries visible holo reflection came back
clear on all 28 photographs, with two orders of magnitude of headroom. #208
moved it here.

**Without one they are reported :data:`ConditionVerdict.UNDETERMINED` with a
reason**, which is what the acceptance criterion asks for and what spec §2.7
requires: a gate that answered "no sleeve" without having looked for one would
be exactly the confidently-wrong output the specification forbids. That is the
whole degradation path — a detector that cannot find a card hands back
:class:`~tcg_domain.confidence.InsufficientInformation`, this module says so on
all six conditions, and nothing anywhere guesses a quadrilateral.

**`geometry` takes three states, and the third is why it is not just
`CardGeometry | None`.** `None` means no detector ran at all; an
:class:`~tcg_domain.confidence.InsufficientInformation` means one ran and could
not locate the card, and its reason is what gets stored; a
:class:`~tcg_domain.card_geometry.CardGeometry` means the six are answered.
Folding the first two together would lose the only explanation a stored row
would ever carry.

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
from tcg_domain.card_geometry import CardGeometry
from tcg_domain.confidence import InsufficientInformation, Uncertain
from tcg_domain.image_quality import (
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

#: Why the six could not be answered, when no detector ran at all.
#: One sentence, stored on the image, naming the thing that is missing rather
#: than the issue number — the record outlives the issue tracker. A detector
#: that *did* run and failed supplies its own reason instead.
_NO_GEOMETRY: Final = "the card has not been located in the photograph"


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
    geometry: Uncertain[CardGeometry] | None = None,
) -> QualityReport:
    """Judge one photograph against spec §19's eleven conditions.

    Args:
        data: The stored image, JPEG or PNG.
        thresholds: Where each condition crosses into poor and then unusable.
            Recorded in the report, so the verdict explains itself later.
        geometry: Where the card is, from `tcg_ml_card_detection.detect`. A
            :class:`~tcg_domain.card_geometry.CardGeometry` decides the six
            conditions that need the card located; an
            :class:`~tcg_domain.confidence.InsufficientInformation` and `None`
            both leave them undetermined, with different reasons.

    Returns:
        A report carrying all eleven conditions, a `[0, 1]` score and the
        versions and thresholds that produced them.

    Raises:
        UnreadableImage: If the bytes do not decode.
    """
    colour = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if colour is None:
        raise UnreadableImage("The stored image could not be decoded.")

    original_height, original_width = colour.shape[:2]
    work = _working(colour, long_edge=thresholds.work_long_edge)
    gray: _Gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

    measurements = _measure(gray, short_edge=min(original_width, original_height))
    located = geometry if isinstance(geometry, CardGeometry) else None
    if located is not None:
        measurements |= _measure_located(work, gray, located, thresholds)

    decided = _DECIDED if located is None else _DECIDED + _NEEDS_THE_CARD
    findings = [_finding(condition, measurements, thresholds) for condition in decided]
    findings.extend(
        QualityFinding(
            condition=condition,
            verdict=ConditionVerdict.UNDETERMINED,
            reason=_why_not_decided(geometry),
        )
        for condition in sorted(set(QualityCondition) - set(decided))
    )

    return QualityReport(
        findings=tuple(sorted(findings, key=lambda f: list(QualityCondition).index(f.condition))),
        score=_score(measurements, thresholds, decided),
        version=IMAGE_QUALITY_VERSION,
        thresholds={**thresholds.as_record(), **(located.thresholds if located else {})},
        detector=None if located is None else located.detector,
    )


def _why_not_decided(geometry: Uncertain[CardGeometry] | None) -> str:
    """What to store against a condition the card boundary would have answered.

    A detector that ran and failed knows more than this module does about why,
    so its reason is preferred over the generic one. `undetermined` with no
    explanation is a gap wearing an answer's clothes.
    """
    if isinstance(geometry, InsufficientInformation) and (geometry.reason or "").strip():
        return geometry.reason.strip() if geometry.reason else _NO_GEOMETRY
    return _NO_GEOMETRY


# ---------------------------------------------------------------------------
# Measuring.
#
# One decode and one downscale producing every number, so that adding a twelfth
# condition does not add a twelfth decode. Glare walks the pixels a second time
# because it walks a different set of them — the card's, once the detector has
# said which those are. The numbers are separated from the thresholds
# deliberately: what was measured is a fact about the photograph and is
# persisted, where what it means is policy and can be recalibrated.
# ---------------------------------------------------------------------------


def _working(colour: _Gray, *, long_edge: int) -> _Gray:
    """The photograph, in colour, scaled so the long edge is at most `long_edge`.

    Scaled because none of the measurements below are scale-invariant: an
    identical photograph at 8 and at 48 megapixels would otherwise get two
    different blur verdicts, and the second is the one a modern phone takes.
    `INTER_AREA` because it is the correct filter for shrinking — a nearest or
    linear downscale aliases fine texture into what looks like sharpness.

    Never enlarged. Upscaling a small photograph would invent detail and hide
    the very thing `low_resolution` exists to catch.

    Colour rather than grayscale because glare needs the chroma (#208), and the
    caller derives the gray copy from this one. Downscaling and the grayscale
    conversion are both linear, so which runs first changes nothing but uint8
    rounding — the ordering was swapped at #208 to avoid resizing twice.
    """
    height, width = colour.shape[:2]
    scale = long_edge / max(height, width)
    if scale >= 1.0:
        return colour
    resized: _Gray = cv2.resize(
        colour,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized


def _measure_located(
    work: _Gray, gray: _Gray, geometry: CardGeometry, thresholds: QualityThresholds
) -> dict[QualityCondition, float]:
    """Everything a located card unlocks.

    Two different kinds of measurement, kept visibly apart:
    :func:`_measure_geometry` reads properties off the quadrilateral and touches
    no pixels, and glare reads the card's pixels and needs the quadrilateral
    only to know which ones they are.
    """
    return {
        **_measure_geometry(geometry),
        QualityCondition.GLARE: _reflecting_fraction(
            work,
            gray,
            _card_mask(
                gray.shape[:2], geometry, inset_fraction=thresholds.glare_card_inset_fraction
            ),
            level=thresholds.glare_level,
            multiple=thresholds.glare_sweep_multiple,
        ),
    }


def _measure_geometry(geometry: CardGeometry) -> dict[QualityCondition, float]:
    """The five of the six that are questions about the quadrilateral itself.

    Every one of them is a property of
    :class:`~tcg_domain.card_geometry.CardGeometry` rather than arithmetic done
    here, so that this module and perspective correction cannot disagree about
    what the same quadrilateral means.
    """
    return {
        QualityCondition.SEVERE_PERSPECTIVE_DISTORTION: geometry.opposite_side_ratio,
        QualityCondition.CARD_PARTLY_OUTSIDE_FRAME: geometry.border_margin_fraction,
        QualityCondition.MULTIPLE_CARDS: float(geometry.candidates),
        QualityCondition.SLEEVE_OBSTRUCTION: geometry.enclosing_ratio,
        QualityCondition.INSUFFICIENT_CARD_SIZE: geometry.area_fraction,
    }


def _measure(gray: _Gray, *, short_edge: int) -> dict[QualityCondition, float]:
    """Every condition the frame alone answers, in one pass."""
    low, high = (float(value) for value in np.percentile(gray, (5, 95)))
    return {
        # The *original* short edge, not the working copy's: this is the one
        # measurement that is about the file rather than about the picture.
        QualityCondition.LOW_RESOLUTION: float(short_edge),
        QualityCondition.BLUR: float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        QualityCondition.EXCESSIVE_DARKNESS: float(gray.mean()),
        QualityCondition.EXCESSIVE_BRIGHTNESS: float(gray.mean()),
        QualityCondition.POOR_EXPOSURE: high - low,
    }


#: The card's long edge over its short edge — 88 mm by 63 mm. Used to recover
#: the measured region's short side from its area, which is what the chroma
#: blur below is scaled against. The region is the card inset by the same amount
#: on all four sides, so it keeps the card's proportions closely enough.
_CARD_ASPECT: Final = 88.0 / 63.0

#: How much of the measured region's short side the chroma field is blurred by.
#: Large enough to erase print — artwork, text and the rosette of the foil
#: pattern itself — and small enough to leave a reflection spanning a fifth of
#: the card standing. Part of the measurement's definition, like the 5th and
#: 95th percentiles above, rather than something a deployment tunes.
_SWEEP_SIGMA_FRACTION: Final = 1.0 / 24.0


def _card_mask(shape: tuple[int, int], geometry: CardGeometry, *, inset_fraction: float) -> _Gray:
    """The card's own pixels in the working copy, inset on every side.

    The quadrilateral is scaled per axis out of the geometry's frame into the
    working copy's — a rescale within the original photograph's single frame,
    computed here at the one call site that needs it. It is not a projection
    between the photograph and the normalized artifact, and no helper that does
    that is to be written (#180).

    Inset rather than taken whole because the card's outer millimetres are not
    the surface being judged: the printed border carries a chroma gradient of
    its own, and #207 measured the detector's extraction passes disagreeing
    about where the card edge is by up to 3 mm, so the outermost band may not be
    card at all. Eroded rather than scaled toward the centroid, because under
    perspective those are two different insets.
    """
    height, width = shape
    corners = np.array(
        [
            (x * width / geometry.frame_width, y * height / geometry.frame_height)
            for x, y in geometry.corners
        ],
        dtype=np.float32,
    )
    mask: _Gray = np.zeros(shape, dtype=np.uint8)
    cv2.fillConvexPoly(mask, corners.round().astype(np.int32), 255)

    longest = max(
        float(np.linalg.norm(corners[index] - corners[(index + 1) % 4])) for index in range(4)
    )
    radius = round(inset_fraction * longest)
    if radius < 1:
        return mask
    element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    eroded: _Gray = cv2.erode(mask, element)
    return eroded


def _reflecting_fraction(
    work: _Gray, gray: _Gray, mask: _Gray, *, level: int, multiple: float
) -> float:
    """The biggest connected region of the card returning light, as a fraction of it.

    The largest *component*, not the total, and the distinction is the whole
    heuristic. A card with a white border and white text has bright flat pixels
    scattered all over it and no glare at all; one sheet of light returned off
    the foil is a single region and ruins the surface signal underneath it.

    Two things count as returning light, in one mask so that one number can say
    so. A pixel at or above `level` is blown out — the specular highlight the
    gate has always caught. And a pixel where the card's colour is sweeping
    faster than `multiple` times its own median rate is diffuse holo sheen: a
    foil layer is a diffraction grating, so the light it returns is spread into
    spectral orders and lands as a smooth band of hue running across the print
    rather than with it. #190 measured that the first test alone never fires on
    real photographs — never above 6.4e-05 of the frame against a line of 0.005
    — because sheen does not clip and the background dilutes what does.

    The rate is measured against the card's *own* median rather than a fixed
    level, because a foil card's whole surface sweeps more than a plain card's
    and an absolute level ranks finishes instead of reflections. Being a ratio
    against the same field, it needs no floor for a nearly colourless
    photograph: sensor and compressor noise raise the median exactly as much as
    they raise everything else, and a photograph with no colour at all has a
    sweep of zero everywhere, which is not greater than zero.
    """
    card = mask > 0
    area = float(card.sum())
    if area <= 0.0:
        return 0.0

    short_edge = float(np.sqrt(area / _CARD_ASPECT))
    sweep = _chroma_sweep(work, sigma=max(1.0, short_edge * _SWEEP_SIGMA_FRACTION))
    reflecting = ((sweep > float(np.median(sweep[card])) * multiple) | (gray >= level)) & card

    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        reflecting.astype(np.uint8), connectivity=8
    )
    if count <= 1:
        return 0.0
    # Row 0 is the background component; CC_STAT_AREA is column 4.
    return float(stats[1:, cv2.CC_STAT_AREA].max()) / area


def _chroma_sweep(work: _Gray, *, sigma: float) -> _Gray:
    """How fast colour is changing, once print structure has been blurred away.

    The gradient magnitude of the blurred `(a, b)` plane of CIELAB. Lightness is
    deliberately excluded: it is where the artwork lives, and a bright edge in
    the illustration is not a reflection. Both chroma axes are summed before the
    square root so the answer is the magnitude of one gradient in the colour
    plane, not two gradients added.
    """
    lab = cv2.cvtColor(work, cv2.COLOR_BGR2Lab).astype(np.float32)
    squared = np.zeros(work.shape[:2], dtype=np.float32)
    for channel in (1, 2):
        smooth = cv2.GaussianBlur(lab[:, :, channel], (0, 0), sigmaX=sigma)
        squared += cv2.Sobel(smooth, cv2.CV_32F, 1, 0) ** 2
        squared += cv2.Sobel(smooth, cv2.CV_32F, 0, 1) ** 2
    swept: _Gray = np.sqrt(squared)
    return swept


# ---------------------------------------------------------------------------
# Judging.
# ---------------------------------------------------------------------------

#: The conditions the frame alone answers. The order is spec §19's, so a report
#: reads the way the specification does.
_DECIDED: Final = (
    QualityCondition.BLUR,
    QualityCondition.LOW_RESOLUTION,
    QualityCondition.POOR_EXPOSURE,
    QualityCondition.EXCESSIVE_DARKNESS,
    QualityCondition.EXCESSIVE_BRIGHTNESS,
)

#: The conditions a located card answers, judged by exactly the same threshold
#: machinery as the five above — one way a condition becomes a finding, not two.
#: Named for what they need rather than for what they measure, because `glare`
#: measures pixels and the other five measure the quadrilateral.
_NEEDS_THE_CARD: Final = (
    QualityCondition.GLARE,
    QualityCondition.SEVERE_PERSPECTIVE_DISTORTION,
    QualityCondition.CARD_PARTLY_OUTSIDE_FRAME,
    QualityCondition.MULTIPLE_CARDS,
    QualityCondition.SLEEVE_OBSTRUCTION,
    QualityCondition.INSUFFICIENT_CARD_SIZE,
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


def _score(
    measurements: dict[QualityCondition, float],
    thresholds: QualityThresholds,
    decided: tuple[QualityCondition, ...],
) -> float:
    """One `[0, 1]` verdict for the photograph, 1 being best.

    The **minimum** over the conditions actually decided, so the weakest link
    governs — the same rule that makes the status the worst finding. That is
    what keeps the two consistent: `score < 0.5` exactly when something was
    detected, which is worth more than an average that lets ten clean
    measurements hide a ruinous eleventh. `decided` is passed rather than read
    from a module constant precisely so that this stays true when the geometric
    five join in: undetermined conditions contribute nothing, because a gate
    that scored an unchecked condition would be inventing a measurement.
    """
    return min(
        _margin(measurements[condition], _limits(condition, thresholds)) for condition in decided
    )
