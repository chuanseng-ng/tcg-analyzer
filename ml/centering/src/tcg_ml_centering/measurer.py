"""Template-aware centering — spec §21, issue #182.

Artifact bytes in, one side's two ratios and a confidence out — or
:class:`~tcg_domain.confidence.InsufficientInformation` when the card's
template offers no frame to measure. `ml/card-detection` is the model for the
shape of this module: no database, no object storage, no HTTP, so everything
worth asserting about it can be asserted by a test that needs no
infrastructure.

**The outer edge is the card frame the caller names, never found here.** The
normalized artifact places the card at a known inner rectangle (#194 put a
2 mm background margin around it), and the caller derives that rectangle from
the artifact's *stored* normalization record — never from the normalizer's
current thresholds, and never from the artifact's own boundary, which since
#194 is 24 px of photographed background away from the card. Only the printed
inner frame needs finding.

**Template-aware means knowing when not to answer** (§21's own list:
full-art, borderless, unusual frame structures). No classifier decides that —
epic #8's decision 1 keeps V1 classical — the geometry does: a template
without a conventional border simply yields no frame-like quadrilateral in the
accepted band, and the answer is `insufficient_information`, never a ratio
measured against a frame that is not there. #176's lesson carries over
unchanged: a dubious frame refuses rather than measuring confidently wrong.

**The arithmetic is the annotation tool's, on purpose.** Borders are the
distances from the midpoints of the found frame's sides to the card
rectangle's matching sides, in artifact pixels — `centeringFromQuads` in
`apps/annotation/lib/annotations.ts`, against an axis-aligned outer quad —
and the ratios are `left / (left + right)` and `top / (top + bottom)`, 0.5
perfect. #188's evaluation compares this package against
`centering_measurements`, so the two derivations must agree; a zero
denominator refuses the axis there and refuses it here.

**Failure is a result, not an exception.** Nothing frame-like found means
:class:`InsufficientInformation` with a reason, never a guessed ratio —
spec §2.7 in general, and §21's borderless case in particular.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from tcg_domain.condition import BoundingBox, Centering
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain

from tcg_ml_centering.thresholds import (
    DEFAULT_CENTERING_THRESHOLDS,
    CenteringThresholds,
)

__all__ = ["SideCentering", "centering_of", "measure"]

_Corner = tuple[float, float]
_Quad = tuple[_Corner, _Corner, _Corner, _Corner]

#: A trading card is 63 x 88 mm. Copied from `ml/card-detection` rather than
#: imported — ml packages deliberately share no code, only domain types.
_CARD_ASPECT: Final = 63.0 / 88.0

#: How far an aspect ratio may sit from a conventional frame's before it
#: scores nothing. Wider than the accept/reject band, because this shapes a
#: confidence rather than making a decision — the detector's convention.
_ASPECT_TOLERANCE: Final = 0.25

#: Said when the bytes did not decode. This package answers rather than
#: raising, so the one place undecodable bytes become a job failure stays
#: upstream of it.
_UNDECODABLE: Final = "the artifact could not be decoded"

#: Said when the card frame names a region too small to hold a measurable
#: card at all.
_CARD_TOO_SMALL: Final = "the card frame names a region too small to measure"

#: Said when no frame-like quadrilateral survived the filters — §21's
#: full-art, borderless and unrecognised templates, refused rather than
#: measured against a frame that is not there.
_NO_FRAME: Final = (
    "no printed border frame was found — a full-art, borderless or "
    "unrecognised template is not measured against a frame it does not have"
)

#: Said, per axis, when the frame touches the card edge and leaves no border
#: to ratio — #160's zero-denominator refusal, never a `0.0`.
_BORDERLESS_AXIS: Final = (
    "the frame touches the card edge on this axis, so there is no border to ratio"
)

#: Said when the found quadrilateral implies a border no real layout has —
#: it is an artwork window or a text box wearing frame-like proportions, and
#: one absurd border poisons the other axis too (#176's lesson).
_IMPLAUSIBLE_FRAME: Final = (
    "the frame found implies an implausibly thick border, so it is an "
    "artwork window rather than the card's border"
)


@dataclass(frozen=True, slots=True)
class SideCentering:
    """One side's two centering ratios and how sure the frame finding was.

    The in-process half of :class:`tcg_domain.condition.Centering`, per side:
    `horizontal` is `left / (left + right)` and `vertical` is
    `top / (top + bottom)`, both in artifact pixels, 0.5 perfect. An axis the
    template does not support is :class:`InsufficientInformation` per ratio.

    Raises:
        ValueError: If a measured ratio is outside ``[0, 1]`` or nothing at
            all was measured — a confidence over zero measurements is a
            confidence about nothing, and the whole-side refusal is
            `InsufficientInformation` from :func:`measure` instead
            (`Centering`'s own rule, mirrored per side).
    """

    horizontal: Uncertain[float]
    vertical: Uncertain[float]
    confidence: Confidence

    def __post_init__(self) -> None:
        measured = 0
        for name in ("horizontal", "vertical"):
            ratio = getattr(self, name)
            if isinstance(ratio, InsufficientInformation):
                continue
            if not 0.0 <= ratio <= 1.0:
                raise ValueError(f"{name} must lie in [0, 1], got {ratio!r}")
            measured += 1
        if measured == 0:
            raise ValueError(
                "a side with nothing measured is the whole-side refusal — "
                "spell it InsufficientInformation instead"
            )


def measure(
    data: bytes,
    *,
    card_frame: BoundingBox,
    thresholds: CenteringThresholds = DEFAULT_CENTERING_THRESHOLDS,
) -> Uncertain[SideCentering]:
    """Measure one side's centering from its normalized artifact.

    Args:
        data: The artifact's encoded bytes.
        card_frame: Where the card sits in the artifact, as fractions of the
            unit square — derived from the artifact's stored normalization
            record. `BoundingBox(0, 0, 1, 1)` is a pre-#194 artifact whose
            card really does reach the edges.
        thresholds: What counts as a frame. Replace wholesale or not at all.

    Returns:
        The side's ratios, or `InsufficientInformation` naming why not.
    """
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return InsufficientInformation(_UNDECODABLE)

    height, width = decoded.shape[:2]
    left = round(card_frame.x * width)
    top = round(card_frame.y * height)
    crop_width = round(card_frame.width * width)
    crop_height = round(card_frame.height * height)
    if crop_width < 50 or crop_height < 50:
        return InsufficientInformation(_CARD_TOO_SMALL)
    card = decoded[top : top + crop_height, left : left + crop_width]

    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    frame = _inner_frame(gray, thresholds=thresholds)
    if frame is None:
        return InsufficientInformation(_NO_FRAME)

    borders = _borders(frame.quad, width=crop_width, height=crop_height)
    if max(borders) > thresholds.max_border_fraction * crop_width:
        return InsufficientInformation(_IMPLAUSIBLE_FRAME)
    horizontal = _ratio(borders[0], borders[1], floor=thresholds.min_axis_border_px)
    vertical = _ratio(borders[2], borders[3], floor=thresholds.min_axis_border_px)
    if isinstance(horizontal, InsufficientInformation) and isinstance(
        vertical, InsufficientInformation
    ):
        return InsufficientInformation(_BORDERLESS_AXIS)
    return SideCentering(
        horizontal=horizontal,
        vertical=vertical,
        confidence=_confidence(frame),
    )


def _ratio(near: float, far: float, *, floor: float) -> Uncertain[float]:
    """One axis's ratio, or the refusal where the denominator says nothing."""
    if near + far < floor:
        return InsufficientInformation(_BORDERLESS_AXIS)
    return near / (near + far)


def centering_of(
    front: Uncertain[SideCentering],
    back: Uncertain[SideCentering],
) -> Uncertain[Centering]:
    """Two sides' measurements as spec §13's centering block.

    The block's confidence is the **minimum** over the measured sides — a
    difference of two estimates is no better than its weaker side, and never
    a product (the economic engine's rule, reused). A refused side
    contributes its refusal per ratio, wearing its own reason; both sides
    refused is the whole-axis refusal, which `Centering` itself refuses to
    hold.
    """
    if isinstance(front, InsufficientInformation) and isinstance(back, InsufficientInformation):
        return InsufficientInformation(front.reason)

    def ratios(side: Uncertain[SideCentering]) -> tuple[Uncertain[float], Uncertain[float]]:
        if isinstance(side, InsufficientInformation):
            return (InsufficientInformation(side.reason), InsufficientInformation(side.reason))
        return (side.horizontal, side.vertical)

    front_horizontal, front_vertical = ratios(front)
    back_horizontal, back_vertical = ratios(back)
    confidence = min(
        side.confidence for side in (front, back) if not isinstance(side, InsufficientInformation)
    )
    return Centering(
        front_horizontal=front_horizontal,
        front_vertical=front_vertical,
        back_horizontal=back_horizontal,
        back_vertical=back_vertical,
        confidence=confidence,
    )


@dataclass(frozen=True, slots=True)
class _Frame:
    """One frame-like quadrilateral, in card-crop coordinates."""

    quad: _Quad
    area: float
    rectangularity: float
    aspect: float


def _inner_frame(gray: MatLike, *, thresholds: CenteringThresholds) -> _Frame | None:
    """The card's printed border frame, or `None` when no template has one."""
    height, width = gray.shape[:2]
    card_area = float(height * width)
    smallest = thresholds.min_frame_area_fraction * card_area
    largest = thresholds.max_frame_area_fraction * card_area

    best: _Frame | None = None
    for binary in _binary_maps(gray):
        contours, _hierarchy = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            # Cheapest possible filter first: a printed face yields hundreds
            # of contours and at most one of them is the frame.
            contour_area = float(cv2.contourArea(contour))
            if not smallest <= contour_area <= largest:
                continue
            candidate = _as_frame(contour, contour_area, thresholds=thresholds)
            if candidate is None or not smallest <= candidate.area <= largest:
                continue
            # ponytail: largest-in-band, no centre clustering — the closed
            # Canny ribbon's two walls sit 1-2 px apart (~0.17 mm), so the
            # outermost is the printed boundary the annotator traces, and the
            # ambiguity is far inside ADR 0010's ~3.6 px discrimination
            # margin. Subtract half the ribbon if #188 ever shows a bias.
            if best is None or candidate.area > best.area:
                best = candidate
    return best


def _binary_maps(gray: MatLike) -> tuple[MatLike, ...]:
    """The extraction passes, ORed by the caller taking any pass's frame.

    Two, not the detector's six: the artifact is small, flat and evenly lit
    by construction, and the frame boundary is a printed line — Canny's easy
    case. The passes that exist for dark tables, drop shadows and worn chroma
    solve photograph problems a normalized artifact cannot have.
    """
    kernel = np.ones((3, 3), np.uint8)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    lower = int(max(0.0, 0.66 * median))
    upper = int(min(255.0, 1.33 * median))
    equalised = cv2.GaussianBlur(
        cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray), (5, 5), 0
    )
    return (
        cv2.morphologyEx(cv2.Canny(blurred, lower, upper), cv2.MORPH_CLOSE, kernel),
        cv2.morphologyEx(cv2.Canny(equalised, lower, upper), cv2.MORPH_CLOSE, kernel),
    )


def _as_frame(
    contour: MatLike, contour_area: float, *, thresholds: CenteringThresholds
) -> _Frame | None:
    """One contour as a frame-like quadrilateral, or `None` if it is not one."""
    perimeter = float(cv2.arcLength(contour, closed=True))
    approximation = cv2.approxPolyDP(contour, thresholds.approx_epsilon * perimeter, closed=True)
    if len(approximation) == 4 and cv2.isContourConvex(approximation):
        points = approximation.reshape(4, 2).astype(float)
    else:
        # A frame whose corner is rounded by the printing or nicked by wear
        # approximates to five or six points; its minimal enclosing rectangle
        # is still the right answer, and the rectangularity check refuses the
        # shapes for which it is not.
        points = cv2.boxPoints(cv2.minAreaRect(contour)).astype(float)

    quad = _clockwise_from_top_left(points)
    area = _area(quad)
    if area <= 0.0:
        return None
    rectangularity = contour_area / area
    if rectangularity < thresholds.min_rectangularity:
        return None

    sides = _side_lengths(quad)
    long_edge = max(sides)
    if long_edge <= 0.0:
        return None
    aspect = min(sides) / long_edge
    if not thresholds.min_aspect <= aspect <= thresholds.max_aspect:
        return None

    return _Frame(
        quad=quad,
        area=area,
        rectangularity=min(1.0, rectangularity),
        aspect=aspect,
    )


def _borders(quad: _Quad, *, width: int, height: int) -> tuple[float, float, float, float]:
    """The four border widths — left, right, top, bottom — in artifact pixels.

    The annotation tool's midpoint rule (`centeringFromQuads`), collapsed for
    an axis-aligned outer quad: the distance from the midpoint of each frame
    side to the card rectangle's matching side. Unsigned, like the tool's, and
    matched by the shared clockwise-from-top-left order. Rotating card and
    frame together changes nothing, which is the point.
    """
    top_mid = _midpoint(quad[0], quad[1])
    right_mid = _midpoint(quad[1], quad[2])
    bottom_mid = _midpoint(quad[2], quad[3])
    left_mid = _midpoint(quad[3], quad[0])
    return (
        abs(left_mid[0]),
        abs(width - right_mid[0]),
        abs(top_mid[1]),
        abs(height - bottom_mid[1]),
    )


def _midpoint(first: _Corner, second: _Corner) -> _Corner:
    return ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)


def _confidence(frame: _Frame) -> Confidence:
    """How frame-like the chosen quadrilateral is.

    ponytail: a heuristic score in `[0, 1]`, not a calibrated probability —
    the detector's recipe verbatim, and nothing acts on a threshold over it.
    Spec §26's accuracy work is where calibration belongs.
    """
    closeness = 1.0 - min(1.0, abs(frame.aspect - _CARD_ASPECT) / _ASPECT_TOLERANCE)
    return Confidence.of(min(1.0, max(0.0, 0.5 * frame.rectangularity + 0.5 * closeness)))


# ---------------------------------------------------------------------------
# Quadrilateral arithmetic, copied from `ml/card-detection` rather than
# imported — the `orderedQuad` precedent: ml packages share no code, and the
# annotation client already carries the same ordering rule for the same
# reason.
# ---------------------------------------------------------------------------


def _clockwise_from_top_left(points: MatLike) -> _Quad:
    """Four points as a deterministic clockwise cycle starting at the top left.

    Sorting by the angle around the centroid fixes the *cycle* (in image
    coordinates ascending angle is clockwise on screen); rotating it so it
    begins at the corner nearest the origin fixes the *phase*, which is what
    matches frame sides to card sides positionally in :func:`_borders`.
    """
    centre_x = float(np.mean(points[:, 0]))
    centre_y = float(np.mean(points[:, 1]))
    cycle = sorted(
        ((float(x), float(y)) for x, y in points),
        key=lambda point: math.atan2(point[1] - centre_y, point[0] - centre_x),
    )
    start = min(range(4), key=lambda index: (sum(cycle[index]), cycle[index][1], cycle[index][0]))
    ordered = cycle[start:] + cycle[:start]
    return (ordered[0], ordered[1], ordered[2], ordered[3])


def _area(quad: _Quad) -> float:
    total = sum(
        quad[index][0] * quad[(index + 1) % 4][1] - quad[(index + 1) % 4][0] * quad[index][1]
        for index in range(4)
    )
    return abs(total) / 2.0


def _side_lengths(quad: _Quad) -> tuple[float, float, float, float]:
    lengths = tuple(
        math.hypot(
            quad[(index + 1) % 4][0] - quad[index][0], quad[(index + 1) % 4][1] - quad[index][1]
        )
        for index in range(4)
    )
    return (lengths[0], lengths[1], lengths[2], lengths[3])
