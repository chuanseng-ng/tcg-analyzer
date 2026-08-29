"""Card boundary detection — spec §18, issue #37.

Bytes in, a :class:`~tcg_domain.card_geometry.CardGeometry` out — or
:data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION` when there is no card to
find. `ml/image-quality` is the model for the shape of this module and for the
same reason: no database, no object storage, no HTTP, so everything worth
asserting about it can be asserted by a test that needs no infrastructure.

**Four extraction passes, ORed, and only the first is the easy one.** A plain
Canny edge map finds a card front lying on a contrasting surface, and nothing
else needs saying about that case. #37 names two harder ones: a dark card on a
dark table, whose boundary gradient is almost nothing, and a card *back*, which
is one flat field with none of a front's internal structure. A CLAHE-equalised
Canny pass handles the first by amplifying local contrast before the edge
detector sees it; an Otsu region pass handles the second by splitting the
picture into two tonal populations instead of looking for a gradient at all, in
both polarities since either the card or the surface may be the brighter one.
#176 names two more, both close-range: a card whose luminance contrast wear has
taken, and a card merged with its own shadow. A saturation pass handles both —
a card face is saturated where a white surface and the card's own grey shadow
are not. Any pass may find the card, and their results are pooled and then
grouped.

**A frame-filling quadrilateral is refused, not returned.** #176's shadow-merged
close-ups fitted a quadrilateral running to the frame's own corner and reported
it at 76-82% confidence — the confidently wrong answer this project's
invariants forbid, because the artifact warped from it mis-frames every
downstream coordinate. A candidate that both touches the frame boundary and
fills most of the frame is the picture's boundary wearing card-like
proportions: it is dropped before grouping, and when nothing else was found the
answer is `insufficient_information`.

**Concentric quadrilaterals are one card, not two.** A sleeve, a top-loader, and
the inner and outer walls of a Canny edge ribbon all produce a second
quadrilateral around the first. Counting those as two cards would refuse the
photograph for `multiple_cards`, which is the opposite of the truth. Candidates
are therefore grouped by centre, a group is one card, and the *spread* within
the winning group is what :attr:`CardGeometry.enclosing_ratio` reports — which
is how sleeve obstruction gets answered at all.

**The boundary is the outermost quadrilateral of that group, on purpose.** The
issue is explicit: do not crop tight to the detected boundary, because M7's edge
and corner analysis needs the card's actual edge and a tight crop shaves the
whitening that matters most.

**Failure is a result, not an exception.** Nothing card-like found means
:data:`INSUFFICIENT_INFORMATION` with a reason, never a guessed quadrilateral —
#37's acceptance criterion in as many words, and spec §2.7 in general. The gate
then reports the five geometric conditions `undetermined`, which is the
degradation path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from tcg_domain.card_geometry import CardGeometry, Corner
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain

from tcg_ml_card_detection.thresholds import (
    CARD_ASPECT,
    CARD_DETECTION_VERSION,
    DEFAULT_DETECTION_THRESHOLDS,
    DetectionThresholds,
)

__all__ = ["detect"]

#: OpenCV's own array type — the same choice `ml/image-quality` makes, and for
#: the same reason: a narrower alias would be describing the library rather than
#: this module.
_Gray = MatLike

_Quad = tuple[Corner, Corner, Corner, Corner]

#: Said when nothing card-like survived the filters.
_NOTHING_FOUND: Final = "no card-like quadrilateral was found in the photograph"

#: Said when everything card-like that was found hugged the frame — #176's
#: shadow-merged blob, or a scan with no border.
_FRAME_FILLING: Final = (
    "only a frame-filling quadrilateral was found, which is the picture's own "
    "boundary rather than a card"
)

#: Said when the bytes did not decode. The gate raises for this case; this
#: package answers rather than racing it to the exception, so that the one place
#: undecodable bytes become a job failure stays
#: `tcg_ml_image_quality.UnreadableImage`.
_UNDECODABLE: Final = "the photograph could not be decoded"

#: How far an aspect ratio may sit from a real card's before it scores nothing.
#: Wider than the accept/reject band, because this shapes a confidence rather
#: than making a decision.
_ASPECT_TOLERANCE: Final = 0.25

#: How much clearance a corner needs from the frame boundary, as a fraction of
#: the frame's short edge, before it stops costing confidence. The gate's
#: `border_margin_ideal`, and like :data:`_ASPECT_TOLERANCE` it shapes a
#: confidence rather than making a decision — the decision is
#: :attr:`DetectionThresholds.frame_margin_fraction`'s.
_EDGE_TOLERANCE: Final = 0.02


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One card-like quadrilateral, in working-copy coordinates."""

    quad: _Quad
    area: float
    centre: Corner
    rectangularity: float
    aspect: float
    #: Least corner-to-frame-edge gap as a fraction of the frame's short edge —
    #: the working-copy twin of `CardGeometry.border_margin_fraction`.
    boundary_margin: float


def detect(
    data: bytes,
    *,
    thresholds: DetectionThresholds = DEFAULT_DETECTION_THRESHOLDS,
) -> Uncertain[CardGeometry]:
    """Locate the card in one photograph.

    Args:
        data: The stored image, JPEG or PNG.
        thresholds: What counts as a card, and what counts as a sleeve around
            one. Recorded by the caller alongside the verdict it produced.

    Returns:
        A :class:`~tcg_domain.card_geometry.CardGeometry` whose corners are in
        the original photograph's coordinates, clockwise from the top left, or
        :class:`~tcg_domain.confidence.InsufficientInformation` when no card
        could be located.
    """
    colour = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if colour is None:
        return InsufficientInformation(_UNDECODABLE)

    original_height, original_width = colour.shape[:2]
    gray, saturation, scale = _working_copies(colour, long_edge=thresholds.work_long_edge)
    height, width = gray.shape[:2]

    candidates = _candidates(gray, saturation, thresholds=thresholds)
    if not candidates:
        return InsufficientInformation(_NOTHING_FOUND)

    grounded = [
        candidate
        for candidate in candidates
        if not _hugs_frame(candidate, frame_area=float(height * width), thresholds=thresholds)
    ]
    if not grounded:
        return InsufficientInformation(_FRAME_FILLING)

    groups = _group_by_centre(
        grounded, tolerance=thresholds.duplicate_centre_fraction * min(width, height)
    )
    card_group = max(groups, key=lambda group: max(member.area for member in group))
    card = max(card_group, key=lambda member: member.area)

    return CardGeometry(
        corners=_rescaled(card.quad, scale=scale, width=original_width, height=original_height),
        confidence=_confidence(card),
        frame_width=original_width,
        frame_height=original_height,
        detector=CARD_DETECTION_VERSION,
        candidates=len(groups),
        enclosing_ratio=_enclosing_ratio(card_group, thresholds=thresholds),
        thresholds=thresholds.as_record(),
    )


# ---------------------------------------------------------------------------
# Finding quadrilaterals.
# ---------------------------------------------------------------------------


def _working_copies(colour: _Gray, *, long_edge: int) -> tuple[_Gray, _Gray, float]:
    """Grayscale and saturation, scaled to at most `long_edge`, and the scale.

    Downscaled because contour extraction on a 48-megapixel photograph is both
    slower and *worse*: sensor noise becomes contours, and the morphology kernel
    sizes below are pixel counts calibrated against this working size. Never
    enlarged. The colour image is resized once and both channels derive from
    the same copy, so the maps agree pixel for pixel. The scale is returned so
    corners can be put back into the original's coordinates, which is the only
    space anything downstream works in.
    """
    height, width = colour.shape[:2]
    scale = long_edge / max(height, width)
    if scale >= 1.0:
        scale = 1.0
    else:
        colour = cv2.resize(
            colour,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray: _Gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
    saturation: _Gray = cv2.cvtColor(colour, cv2.COLOR_BGR2HSV)[:, :, 1]
    return gray, saturation, scale


def _binary_maps(gray: _Gray, saturation: _Gray) -> tuple[_Gray, ...]:
    """The four extraction passes, as maps `findContours` can walk.

    Five maps rather than four: the Otsu pass contributes both polarities. See
    the module docstring for why there are four. The closing kernel joins
    an edge that a compression artifact or a soft focus left with a gap in it —
    without it a card is found as four unconnected lines and no quadrilateral at
    all.
    """
    kernel = np.ones((3, 3), np.uint8)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Canny's two levels from the image's own median, so a dark photograph is
    # not measured against a bright one's thresholds.
    median = float(np.median(blurred))
    lower = int(max(0.0, 0.66 * median))
    upper = int(min(255.0, 1.33 * median))

    equalised = cv2.GaussianBlur(
        cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray), (5, 5), 0
    )
    _level, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    region: _Gray = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel)

    return (
        cv2.morphologyEx(cv2.Canny(blurred, lower, upper), cv2.MORPH_CLOSE, kernel),
        cv2.morphologyEx(cv2.Canny(equalised, lower, upper), cv2.MORPH_CLOSE, kernel),
        # Both polarities: which of the card and the background Otsu calls
        # "foreground" depends on which is brighter, and both cases happen.
        region,
        cv2.bitwise_not(region),
        # The saturation pass, in the direct polarity only: a grey card on a
        # saturated surface is speculative, and every case #176 names is a
        # saturated card on a grey one.
        cv2.morphologyEx(
            cv2.threshold(saturation, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.MORPH_CLOSE,
            kernel,
        ),
    )


def _candidates(
    gray: _Gray, saturation: _Gray, *, thresholds: DetectionThresholds
) -> list[_Candidate]:
    """Every card-like quadrilateral any pass found, before grouping."""
    height, width = gray.shape[:2]
    frame_area = float(height * width)
    smallest = thresholds.min_area_fraction * frame_area
    largest = thresholds.max_area_fraction * frame_area

    found: list[_Candidate] = []
    for binary in _binary_maps(gray, saturation):
        # RETR_LIST rather than RETR_EXTERNAL: a sleeve is *inside* the outline
        # of nothing, but a card is inside a sleeve, and the enclosed one is the
        # one this package exists to find.
        contours, _hierarchy = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            # Cheapest possible filter first: a checkerboard yields thousands of
            # contours and none of them is a card.
            contour_area = float(cv2.contourArea(contour))
            if not smallest <= contour_area <= largest:
                continue
            candidate = _as_candidate(
                contour, contour_area, width=width, height=height, thresholds=thresholds
            )
            if candidate is not None and smallest <= candidate.area <= largest:
                found.append(candidate)
    return found


def _as_candidate(
    contour: MatLike,
    contour_area: float,
    *,
    width: int,
    height: int,
    thresholds: DetectionThresholds,
) -> _Candidate | None:
    """One contour as a card-like quadrilateral, or `None` if it is not one."""
    perimeter = float(cv2.arcLength(contour, closed=True))
    approximation = cv2.approxPolyDP(contour, thresholds.approx_epsilon * perimeter, closed=True)
    if len(approximation) == 4 and cv2.isContourConvex(approximation):
        points = approximation.reshape(4, 2).astype(float)
    else:
        # A card whose corner is rounded, occluded by a finger, or lost to a
        # compression artifact approximates to five or six points. Its minimal
        # enclosing rectangle is still the right answer; the rectangularity
        # check below is what refuses the shapes for which it is not.
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

    gaps = [gap for x, y in quad for gap in (x, y, width - x, height - y)]
    return _Candidate(
        quad=quad,
        area=area,
        centre=_centre(quad),
        rectangularity=min(1.0, rectangularity),
        aspect=aspect,
        boundary_margin=max(0.0, min(gaps)) / float(min(width, height)),
    )


# ---------------------------------------------------------------------------
# Ordering, grouping and scoring.
# ---------------------------------------------------------------------------


def _clockwise_from_top_left(points: MatLike) -> _Quad:
    """Four points as a deterministic clockwise cycle starting at the top left.

    Two steps, and both are needed. Sorting by the angle around the centroid
    fixes the *cycle*: in image coordinates, where y increases downward,
    ascending angle is clockwise on screen. Rotating that cycle so it begins at
    the corner nearest the frame's origin fixes the *phase*, which is what makes
    the order the same for the same card photographed twice.

    Perspective correction reads these positionally, so an inconsistent order
    does not fail — it silently rotates or mirrors the card.

    ponytail: the phase choice degenerates for a card rotated near 45 degrees,
    where two corners are almost equally close to the origin. A learned detector
    that predicts corner identities directly is M7's option if that ever matters.
    """
    centre_x = float(np.mean(points[:, 0]))
    centre_y = float(np.mean(points[:, 1]))
    cycle = sorted(
        ((float(x), float(y)) for x, y in points),
        key=lambda point: math.atan2(point[1] - centre_y, point[0] - centre_x),
    )
    # Ties broken by y and then x so that two runs over the same photograph
    # cannot disagree.
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


def _centre(quad: _Quad) -> Corner:
    return (
        sum(corner[0] for corner in quad) / 4.0,
        sum(corner[1] for corner in quad) / 4.0,
    )


def _hugs_frame(
    candidate: _Candidate, *, frame_area: float, thresholds: DetectionThresholds
) -> bool:
    """Whether this quadrilateral is the picture's own boundary, not a card.

    Both conditions, deliberately: a clipped card touches the frame boundary
    too, but a clipped card does not also fill most of the frame. #176's
    shadow-merged blobs did both — and were returned at 76-82% confidence.

    ponytail: a card that legitimately fills the frame to the very edge is
    refused with everything else that hugs it; a learned detector that can tell
    a card's boundary from the picture's is the upgrade.
    """
    return (
        candidate.boundary_margin <= thresholds.frame_margin_fraction
        and candidate.area >= thresholds.frame_fill_fraction * frame_area
    )


def _group_by_centre(candidates: list[_Candidate], *, tolerance: float) -> list[list[_Candidate]]:
    """Concentric candidates, gathered — one group is one card.

    Single-link clustering on the centre, which is all that is wanted here: the
    inner and outer walls of an edge ribbon, the same card found by three
    passes, and a card inside its sleeve all share a centre, while two cards
    lying side by side do not. This is what stops a sleeve being reported as a
    second card, which the gate would refuse the photograph for.

    ponytail: quadratic in the number of candidates, which is at most a few
    dozen after the area and aspect filters. A grid index if a photograph ever
    produces hundreds.
    """
    groups: list[list[_Candidate]] = []
    for candidate in sorted(candidates, key=lambda member: -member.area):
        for group in groups:
            if any(_gap(candidate.centre, member.centre) <= tolerance for member in group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def _gap(first: Corner, second: Corner) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _enclosing_ratio(group: list[_Candidate], *, thresholds: DetectionThresholds) -> float:
    """How much bigger the outermost concentric quadrilateral is — §19's sleeve.

    `1.0` means nothing plausibly enclosing was found, which is the answer for a
    bare card. The margin floor is what excludes the artifact: the inner and
    outer walls of a closed Canny ribbon are a few pixels apart by construction,
    and reporting that as a sleeve would put a sleeve on every photograph.

    ponytail: the weakest heuristic in this package. Any concentric object of
    roughly card proportions a little larger than the card reads as a sleeve —
    a card resting on a slightly bigger box, say. It costs a `poor`, which is
    "continue but tell the user", never a refusal. M7's segmentation model is
    the upgrade.
    """
    largest = max(member.area for member in group)
    smallest = min(member.area for member in group)
    if smallest <= 0.0:
        return 1.0
    # Half the difference of the side lengths of two squares of these areas —
    # how far the outer quadrilateral stands off the inner one, per side.
    margin = (math.sqrt(largest) - math.sqrt(smallest)) / 2.0
    ratio = largest / smallest
    if margin >= thresholds.sleeve_min_margin and ratio <= thresholds.sleeve_max_ratio:
        return ratio
    return 1.0


def _confidence(card: _Candidate) -> Confidence:
    """How card-like the chosen quadrilateral is.

    ponytail: a heuristic score in `[0, 1]`, not a calibrated probability — it
    has never been compared against a labelled set, because there is not one
    yet. It is used as a signal here and nothing acts on a threshold over it.
    M7's detector supplies a real one, and spec §26's accuracy work is where
    calibration belongs.
    """
    closeness = 1.0 - min(1.0, abs(card.aspect - CARD_ASPECT) / _ASPECT_TOLERANCE)
    # A boundary the quadrilateral shares with the picture is one the detector
    # cannot vouch for (#176): no clearance halves the score, full clearance
    # leaves it untouched.
    clearance = min(1.0, card.boundary_margin / _EDGE_TOLERANCE)
    base = 0.5 * card.rectangularity + 0.5 * closeness
    return Confidence.of(min(1.0, max(0.0, base * (0.5 + 0.5 * clearance))))


def _rescaled(quad: _Quad, *, scale: float, width: int, height: int) -> _Quad:
    """The quadrilateral in the original photograph's coordinates.

    Clamped to the frame, because `approxPolyDP` on a contour that runs along
    the picture's edge can place a corner a pixel outside it, and a corner
    outside the frame is not something a later stage should have to reason
    about.
    """
    corners = tuple(
        (
            min(float(width), max(0.0, x / scale)),
            min(float(height), max(0.0, y / scale)),
        )
        for x, y in quad
    )
    return (corners[0], corners[1], corners[2], corners[3])
