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
are not; its closing kernel is sized for wear (#192), because a worn back is
chroma *fragments* — a border ring severed at whitened corners, a mottled
swirl — that a 3x3 closing never joins into a card. #193 names a sixth: a
light-bordered card on a light table, whose only boundary evidence is its own
drop shadow, a gradient the median-derived Canny levels of a mostly-white
frame sit far above — a fixed low-threshold Canny pass sees it. #335 names a
seventh: a card on glare-lit dark cloth, whose weave the Gaussian-blurred
Canny passes keep as speckle that the closing joins to the card's edge, so
the card is admitted only as part of a blob that is not rectangular — and on
one photograph the artwork window was returned instead. A median blur removes
the speckle and keeps the edge. Any pass may find the card, and their results
are pooled and then grouped.

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

**But concentric is not the same as enclosing, and the spread is mostly this
package disagreeing with itself.** #207: on 21 of the corpus's 28 real
photographs — every one a bare card — the winning group held two clusters of
quadrilaterals 0.9 to 3.0 mm apart, and the ratio between them read as a
sleeve. Neither cluster was a second object. The six passes above answer
slightly different questions about one edge: the fixed-level Canny pass finds
the card's *drop shadow*, an Otsu region takes card and shadow as one
population, the saturation map's closing lands inside a desaturated edge. So a
quadrilateral must now stand off the card **on all four of its sides**, by a
fraction of the card's own long edge, before its ratio is reported — an average
standoff cannot tell a holder, which surrounds the card, from a shadow, which
falls on one side of it. The measured consequence is stated where the threshold
is: this resolves a rigid holder and nothing tighter, a sleeve included.

**A quadrilateral wholly inside another is that card's structure, not a second
card.** #206: a front's artwork window is convex, card-aspect and well filled,
so it survives every candidate filter, and at close range its centre sits far
enough from the card's to escape the concentric grouping — on 4 of the corpus's
28 real photographs it was counted as a second card and the gate refused a
one-card scene for `multiple_cards`. A group whose outermost quadrilateral lies
inside another group's is therefore dropped before counting, with a few pixels
of slack, because the corpus's fourth phantom was the card's own text panel:
a contour that shares the card's edges, whose fitted corners landed 2.8 px
*outside* the winning quadrilateral. Group-level and not candidate-level on
purpose — a card is contained by its sleeve too, but concentrically, so it is
already in the sleeve's group, and dropping contained candidates before
grouping would take the sleeve's answer apart.

**Or almost wholly inside it** (#335): on dark cloth a text panel's fitted
corner landed 40 px past the card, and a panel whose area lies nine-tenths
inside a card-shaped quadrilateral is still that card's structure. Only a
card-shaped one: inside a slab's shell the contained quadrilateral is the
card, which the case rule below must see.

**Never by a quadrilateral that ends on the frame** (#340): on a black surface
ridges in the texture were traced into a quadrilateral that ran to the
picture's edge, cut a corner off the card, and still held nine-tenths of it —
so the card was dropped as its structure and the surface was returned. A
container touching the frame that holds a group clear of it only by that
share, not by its corners, is card plus surface: the container is dropped and
the card kept. #192's reason, lifted from members to groups.

**Unless the container is the one that is not card-shaped.** #320: a grader's
slab is a rigid 3.25 x 5.25-inch shell at aspect 0.619, and on the one real slab
photograph that returned a quadrilateral the card inside it was found — at
0.742 — as a contained group, pushed off the shell's centre by the label and
dropped by the rule above, leaving the shell to be returned as the card at
`good`. So a containing quadrilateral below
:attr:`DetectionThresholds.case_max_aspect`, holding one closer to a card's
proportions than itself, refuses the photograph as a card in a case. #206's
phantoms are the same pair the other way round — on every one of the corpus's
four the container was the card, 0.69-0.72 — which is why the comparison
between the two is required and not only the line. The card stays a contained
group only because grouping does not chain through a member between the two
centres (#327). Refused rather than
analysed through: spec §4 excludes slab analysis. **Never through a strong
keystone** (#325): a tilted bare card's text region can read nearer a card than
the foreshortened card does, so past the gate's perspective unusable line the
question is not asked and the perspective refusal answers instead. **Nor only
across groups** (#330): on angled slabs the card often lands in the shell's own
group, so the same two halves are asked of the returned quadrilateral and its
group's members, with the member required to be well inside by area — and
aspects read from the corners, which for a fallback candidate are not the
admission rectangle's (#324). One angled slab, a group of one, is out of reach
and left to the perspective warning. **Nor only when a card is found** (#332):
a square-on PSA slab's card was never found at all, so a quadrilateral read
square-on, at a slab's proportions and not a card's, is refused on its shape.
Only square-on, because a keystone lowers the aspect too: the angled PSA slabs
read level with warned bare tilts and are left to the perspective warning.

**A group touching the frame with no edge where it ends is not a second card.**
#334: on a bare tilted card the Otsu region pass cut the table's lighting
falloff at the frame's corner into a card-aspect rectangle, 0.077 of the frame
— too small for the frame-filling refusal, outside the card so never
contained — and the gate refused a one-card scene for `multiple_cards`. The
frame supplies its two outer sides; its other two lie where the light fades,
and no edge runs along them. A second card clipped by the frame has edges
there. So such a group is not *counted*, and only a group other than the
card's: a slab photograph's winning quadrilateral has unsupported sides too,
and selection and the case rules above are not touched.

**A square-on quadrilateral too wide for one card is two cards.** #342: two
overlapping cards were traced as one quadrilateral, admitted under the wide
aspect band, and each card inside it was dropped as structure by #206's rule —
so a two-card photograph passed at `good`. Read square-on, where the aspect is
the object's and not the tilt's, a quadrilateral at 0.77 or wider is counted as
two, the mirror of #332's slab shape rule. It is only a count: the gate's
`multiple_cards` refuses, and nothing about selection changes. **Nor only by
shape** (#345): a union of two backs offset diagonally is card-shaped, and
there only the saturation pass's wear closing joined the two, while every
luminance pass traced the upper card inside it. A returned quadrilateral no
other pass traced, holding a luminance-traced one that lies along it, is
counted as two the same way.

**A quadrilateral with the card's border running round it is not the card.**
#341: on a dim surface a back's dark-blue border is the surface's own grey, so
every luminance pass traced the light panel inside it, the saturation pass's
map was noise and never closed the card, and the panel was warped as the card
at `poor`. Its shape gives nothing away. What does is outside it: the border,
still saturated, running round it on at least three sides, with the
unsaturated surface beyond. Then nothing is returned, and the gate refuses as
it does when nothing is found, because the card was not. A card's own
quadrilateral has only the surface outside it, so the rule never refuses one
that was found on its edge.

**The boundary is the outermost quadrilateral of that group, on purpose.** The
issue is explicit: do not crop tight to the detected boundary, because M7's edge
and corner analysis needs the card's actual edge and a tight crop shaves the
whitening that matters most. One exception (#192): a member touching the frame
boundary loses to any member clear of it, however large — the corpus's
card-plus-shadow blobs sat below the frame-filling refusal's area line, and a
quadrilateral that ends on the picture's own edge ends there because the
picture does, not because the card does. And "outermost" means it encloses
(#340): a member is passed over when a corner of a distinct object in its
group — one well inside it by area — lies outside it, as the card's did under
a square-on surface quadrilateral that shared its centre. It is also passed over
for a copy of the same card that reads markedly squarer (#339): two passes do not
disagree about one card's perspective by that much, so the keystone is cloth the
larger one took in. And "largest" is measured on the corners returned, not the
rectangle a fallback candidate was admitted on (#360): that rectangle overstates
a polygon lying inside the card's edge.

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
from tcg_domain.image_quality import CardNotLocated, GateRefusal

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

#: Said when a case was found around the card (#320), rather than the card.
_IN_A_CASE: Final = (
    "the card is inside a rigid case, and the quadrilateral found is the case rather than the card"
)

#: Said when a saturated border runs round the quadrilateral found (#341): it is
#: the card's inner panel, and the card's own edge was not found. The gate
#: reports it as `no_card_found`, the same refusal as finding nothing, because
#: that is what happened to the card.
_BORDER_OUTSIDE: Final = (
    "a saturated border runs round the only card-like quadrilateral found, so it is the "
    "card's inner panel and the card's own edge was not found"
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

#: The saturation map's closing kernel, in pixels at the working scale. Sized
#: for wear rather than for compression gaps (#192): a worn back thresholds to
#: chroma fragments — a border ring severed at whitened corners, a mottled
#: swirl — and the corpus's worn backs merged into one card-shaped region at
#: 9 and not at 3. The other maps keep the 3x3 kernel: edges they leave open
#: are compression artifacts, not wear.
_WEAR_CLOSE: Final = 9

#: The fixed Canny levels of the drop-shadow pass (#193). The other two Canny
#: passes derive their levels from the frame's median so a dark photograph is
#: not measured against a bright one's thresholds — but on a mostly-white
#: frame that median puts both levels far above the soft gradient of a card's
#: drop shadow, which for a light-bordered card on a light table is the only
#: boundary evidence there is. Fixed and low, because the shadow's step is a
#: couple of dozen tones whatever the exposure.
_SHADOW_EDGE_LEVELS: Final = (20, 60)

#: The textured-surface pass's median kernel (#335), in pixels at the working
#: scale. On near-black cloth under glare a 5x5 Gaussian leaves the weave as
#: edge speckle, which the closing joins to the card's edge; 7 removes it and
#: 11 found the same card, so the smaller is kept.
_TEXTURE_MEDIAN: Final = 7

#: How #334's edge support is read: points sampled along a side (its middle
#: 80%), how far from one an edge may lie (a square kernel, in pixels at the
#: working scale), and how close both corners must sit to a frame edge for the
#: side to be the frame's rather than the object's.
_SIDE_SAMPLES: Final = 40
_EDGE_REACH: Final = 7
_ON_FRAME_PX: Final = 4.0

#: Where the saturation pass sits in :func:`_binary_maps`'s tuple (#345).
_SATURATION_MAP: Final = 4

#: How far apart two long axes may lie and still run the same way (#345): the
#: midpoint between parallel and perpendicular, not a fitted value. A second
#: card overlapping the first lies along it (0-3 degrees on the real pairs); a
#: front's artwork window lies across it (87-89).
_PARALLEL_MAX_DEGREES: Final = 45.0

#: Where #341's border band and the surface beyond it are read, as fractions of
#: the quadrilateral's size across the side: three lines each, averaged. The
#: near lines sit inside a real back's border (2.5-6.5% of the card per side),
#: and past the few pixels a card's own quadrilateral sits inside its edge; the
#: far lines lie on the surface.
_BAND_NEAR: Final = (0.025, 0.03, 0.035)
_BAND_FAR: Final = (0.09, 0.10, 0.11)


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
    #: Whether the saturation pass found it (#345): its wear closing is the one
    #: pass that joins two overlapping saturated cards into one region.
    saturation: bool = False


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

    binaries = _binary_maps(gray, saturation)
    candidates = _candidates(gray, binaries, thresholds=thresholds)
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
    # A frame-touching quadrilateral holding a card clear of the frame by
    # overlap alone is card plus surface (#340), and gives way to the card.
    surface = _card_plus_surface(groups, thresholds=thresholds)
    groups = [group for group in groups if not any(group is member for member in surface)]
    contained = _containment(groups, thresholds=thresholds)
    # A card-shaped quadrilateral inside one that is not is a card in a case
    # (#320), and a case is refused rather than returned as the card.
    if any(_is_a_case(outer, inner, thresholds=thresholds) for outer, inner in contained):
        return CardNotLocated(_IN_A_CASE, refusal=GateRefusal.CARD_IN_A_CASE)
    # Any other group wholly inside another group's outermost quadrilateral is
    # that card's own structure — an artwork window, a text panel — never a
    # second card (#206). Dropped before counting and selection alike.
    inside = [inner for _outer, inner in contained]
    groups = [group for group in groups if not any(group is member for member in inside)]
    card_group = max(groups, key=lambda group: max(member.area for member in group))
    # The outermost member clear of the frame boundary, and the outermost of
    # all only when every member touches it (#192) — a clipped card is still
    # returned, but a card-plus-shadow blob whose far corner is the picture's
    # own is never preferred over the card found beside it.
    clear_of_the_boundary = [
        member for member in card_group if member.boundary_margin > thresholds.frame_margin_fraction
    ]
    card = _outermost(clear_of_the_boundary or card_group, thresholds=thresholds)
    # The same case, with its card grouped alongside it rather than contained
    # as a group of its own (#330).
    if _holds_a_card(card, card_group, thresholds=thresholds):
        return CardNotLocated(_IN_A_CASE, refusal=GateRefusal.CARD_IN_A_CASE)
    # And the same case with no card found inside it at all, which only its
    # shape gives away, and only square-on (#332).
    if (
        _quad_aspect(card.quad) < thresholds.case_square_on_max_aspect
        and _opposite_side_ratio(card.quad) <= thresholds.case_square_on_max_perspective_ratio
    ):
        return CardNotLocated(_IN_A_CASE, refusal=GateRefusal.CARD_IN_A_CASE)
    # A saturated border running round what would be returned, with the
    # surface beyond it, is a card's inner panel whose border blended into the
    # surface (#341): the card was not found, so nothing is returned.
    if _border_outside(card.quad, saturation, thresholds=thresholds):
        return InsufficientInformation(_BORDER_OUTSIDE)
    # A frame-touching group with no edge where it ends is lighting on the
    # table, not a second card (#334). Counting only: the card group is never
    # tested, because a slab's own returned quadrilateral has such sides too.
    # The median-level Canny pass is the edge evidence, not a second Canny.
    edges = cv2.dilate(binaries[0], np.ones((_EDGE_REACH, _EDGE_REACH), np.uint8))
    counted = [
        group
        for group in groups
        if group is card_group
        or not _unsupported_at_frame(group, edges=edges, thresholds=thresholds)
    ]
    # A square-on quadrilateral too wide for one card is two overlapping cards
    # traced together (#342): each card inside it was dropped as structure.
    two_cards = (
        _quad_aspect(card.quad) >= thresholds.pair_square_on_min_aspect
        and _opposite_side_ratio(card.quad) <= thresholds.case_square_on_max_perspective_ratio
    ) or _saturation_union(card, grounded, thresholds=thresholds)

    return CardGeometry(
        corners=_rescaled(card.quad, scale=scale, width=original_width, height=original_height),
        confidence=_confidence(card),
        frame_width=original_width,
        frame_height=original_height,
        detector=CARD_DETECTION_VERSION,
        candidates=max(len(counted), 2) if two_cards else len(counted),
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
    """The six extraction passes, as maps `findContours` can walk.

    Seven maps rather than six: the Otsu pass contributes both polarities. See
    the module docstring for why there are five. The 3x3 closing kernel joins
    an edge that a compression artifact or a soft focus left with a gap in it —
    without it a card is found as four unconnected lines and no quadrilateral at
    all. The saturation pass closes at :data:`_WEAR_CLOSE` instead, because the
    gaps it must join are wear, which is wider than any compression artifact.
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
    textured = cv2.medianBlur(gray, _TEXTURE_MEDIAN)
    textured_median = float(np.median(textured))
    median_lower = int(max(0.0, 0.66 * textured_median))
    median_upper = int(min(255.0, 1.33 * textured_median))
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
            np.ones((_WEAR_CLOSE, _WEAR_CLOSE), np.uint8),
        ),
        # The drop-shadow pass (#193): fixed low Canny levels, for the card
        # whose only boundary evidence is the shadow it casts.
        cv2.morphologyEx(cv2.Canny(blurred, *_SHADOW_EDGE_LEVELS), cv2.MORPH_CLOSE, kernel),
        # The textured-surface pass (#335): a median blur removes a cloth's
        # speckle and keeps the card's step edge, so the closing no longer
        # joins the two into one blob.
        #
        # It also keeps a steeply keystoned card's outline above
        # `min_rectangularity` where dark artwork notches every other pass
        # (#338's test). ponytail: the glare-lit speckle merge itself has no
        # synthetic scene (five tried in #335) and rests on two photographs;
        # #343 owns the photographs that would give it a test.
        cv2.morphologyEx(cv2.Canny(textured, median_lower, median_upper), cv2.MORPH_CLOSE, kernel),
    )


def _candidates(
    gray: _Gray, binaries: tuple[_Gray, ...], *, thresholds: DetectionThresholds
) -> list[_Candidate]:
    """Every card-like quadrilateral any pass found, before grouping."""
    height, width = gray.shape[:2]
    frame_area = float(height * width)
    smallest = thresholds.min_area_fraction * frame_area
    largest = thresholds.max_area_fraction * frame_area

    found: list[_Candidate] = []
    for index, binary in enumerate(binaries):
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
                contour,
                contour_area,
                width=width,
                height=height,
                saturation=index == _SATURATION_MAP,
                thresholds=thresholds,
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
    saturation: bool,
    thresholds: DetectionThresholds,
) -> _Candidate | None:
    """One contour as a card-like quadrilateral, or `None` if it is not one."""
    perimeter = float(cv2.arcLength(contour, closed=True))
    approximation = cv2.approxPolyDP(contour, thresholds.approx_epsilon * perimeter, closed=True)
    corners: _Quad | None = None
    if len(approximation) == 4 and cv2.isContourConvex(approximation):
        points = approximation.reshape(4, 2).astype(float)
    else:
        # A card whose corner is rounded, occluded by a finger, or lost to a
        # compression artifact approximates to five or six points. Its minimal
        # enclosing rectangle still decides whether it is a card — the
        # rectangularity, aspect and area below, and the aspect #320's case
        # rule reads — but it is not where the card is (#324): a rectangle has
        # no keystone, so a tilted card read through it reports a perspective
        # ratio of exactly 1.0 and corners that clip one end and overshoot the
        # other. The corners are the four-sided polygon fitted to the contour's
        # hull instead, which follows the keystone.
        #
        # Admission stays on the rectangle on purpose: judged on the tighter
        # polygon, a card-plus-shadow blob clears the rectangularity line, and
        # on the corpus it was then read as a case around the card and refused.
        points = cv2.boxPoints(cv2.minAreaRect(contour)).astype(float)
        corners = _keystone(contour)

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
    if corners is not None:
        quad = corners
    return _Candidate(
        quad=quad,
        area=area,
        centre=_centre(quad),
        rectangularity=min(1.0, rectangularity),
        aspect=aspect,
        boundary_margin=max(0.0, min(gaps)) / float(min(width, height)),
        saturation=saturation,
    )


def _keystone(contour: MatLike) -> _Quad | None:
    """The four-sided polygon closest in area to the contour's hull, or `None`.

    `approxPolyN` contracts the hull's vertices until four remain, each step
    the one adding least area, so a corner lost to rounding or a finger is
    extended back to where the card's two edges meet — on a keystoned card too,
    which a minimum-area rectangle cannot represent. Returned as `(1, 4, 2)`,
    not `approxPolyDP`'s `(4, 1, 2)`.
    """
    polygon = cv2.approxPolyN(contour, 4, ensure_convex=True)
    if polygon is None or polygon.size != 8:
        return None
    return _clockwise_from_top_left(polygon.reshape(4, 2).astype(float))


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


def _unsupported_at_frame(
    group: list[_Candidate], *, edges: _Gray, thresholds: DetectionThresholds
) -> bool:
    """Whether a group touches the frame and no edge runs where it ends (#334).

    Only the sides clear of the frame are read: the frame supplies the others,
    for a phantom and a clipped card alike. One supported side is enough to
    count the group, so a clipped card whose far edge is soft is still a card.
    """
    outer = max(group, key=lambda member: member.area)
    if outer.boundary_margin > thresholds.frame_margin_fraction:
        return False
    height, width = edges.shape[:2]
    for index in range(4):
        first, second = outer.quad[index], outer.quad[(index + 1) % 4]
        if _on_frame(first, second, width=width, height=height):
            continue
        if _edge_support(first, second, edges) >= thresholds.phantom_min_edge_support:
            return False
    return True


def _on_frame(first: Corner, second: Corner, *, width: int, height: int) -> bool:
    """Whether both corners of a side lie against the same frame edge."""

    def gaps(point: Corner) -> tuple[float, float, float, float]:
        return (point[0], point[1], width - point[0], height - point[1])

    return any(
        near <= _ON_FRAME_PX and far <= _ON_FRAME_PX
        for near, far in zip(gaps(first), gaps(second), strict=True)
    )


def _edge_support(first: Corner, second: Corner, edges: _Gray) -> float:
    """The share of a side's sampled points lying on or near an edge."""
    height, width = edges.shape[:2]
    hits = 0
    for step in np.linspace(0.1, 0.9, _SIDE_SAMPLES):
        column = round(first[0] + (second[0] - first[0]) * step)
        row = round(first[1] + (second[1] - first[1]) * step)
        if 0 <= column < width and 0 <= row < height and edges[row, column] > 0:
            hits += 1
    return hits / _SIDE_SAMPLES


def _group_by_centre(candidates: list[_Candidate], *, tolerance: float) -> list[list[_Candidate]]:
    """Concentric candidates, gathered — one group is one card.

    Clustered on the centre of each group's largest member, which is all that
    is wanted here: the inner and outer walls of an edge ribbon, the same card
    found by three passes, and a card inside its sleeve all share a centre,
    while two cards lying side by side do not. This is what stops a sleeve
    being reported as a second card, which the gate would refuse the
    photograph for.

    **Not single-link** (#327): chained through any member, a group drifts. On
    the one real slab a second shell member, its centre moved 23 px by #324's
    keystone corners, bridged the shell to the card 64 px below it, so the card
    was never a contained group and #320's case rule decided on the label.

    ponytail: quadratic in the number of candidates, which is at most a few
    dozen after the area and aspect filters. A grid index if a photograph ever
    produces hundreds.
    """
    groups: list[list[_Candidate]] = []
    for candidate in sorted(candidates, key=lambda member: -member.area):
        for group in groups:
            if _gap(candidate.centre, group[0].centre) <= tolerance:
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def _gap(first: Corner, second: Corner) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _containment(
    groups: list[list[_Candidate]], *, thresholds: DetectionThresholds
) -> list[tuple[_Candidate, list[_Candidate]]]:
    """Every group whose outermost member sits inside another group's, with it.

    Each pair is the containing group's outermost member and the contained
    group. Strictly larger by area, so two groups cannot contain each other.
    The slack is for a phantom whose contour shares the card's own boundary:
    where the two edges coincide, the fitted corners land a couple of pixels
    either side of the winning quadrilateral's, and a strict test would keep
    exactly the shape that is most obviously not a second card.

    Or by overlap, inside a card-shaped container only (#335): a panel whose
    fitted corner juts well past the card is still almost wholly inside it.
    Not inside a case-shaped one, where the contained card is a question for
    `_is_a_case` and a keystoned card can fail it, leaving the shell returned.
    """
    pairs: list[tuple[_Candidate, list[_Candidate]]] = []
    for group in groups:
        inner = max(group, key=lambda member: member.area)
        for other in groups:
            outer = max(other, key=lambda member: member.area)
            if other is group or outer.area <= inner.area:
                continue
            if _encloses(outer.quad, inner.quad, thresholds=thresholds) or _overlaps(
                outer, inner, thresholds=thresholds
            ):
                pairs.append((outer, group))
    return pairs


def _card_plus_surface(
    groups: list[list[_Candidate]], *, thresholds: DetectionThresholds
) -> list[list[_Candidate]]:
    """Every group that is the card plus the surface beside it (#340).

    Its outermost member touches the frame, and holds another group's clear of
    it by #335's overlap share but not by its corners: a quadrilateral traced
    along the surface that cuts the card. A card's own panel never lies clear
    of a card that is not, so #335's case is untouched.
    """
    surfaces: list[list[_Candidate]] = []
    for other in groups:
        outer = max(other, key=lambda member: member.area)
        if outer.boundary_margin > thresholds.frame_margin_fraction:
            continue
        for group in groups:
            inner = max(group, key=lambda member: member.area)
            if (
                group is not other
                and inner.area < outer.area
                and inner.boundary_margin > thresholds.frame_margin_fraction
                and _overlaps(outer, inner, thresholds=thresholds)
                and not _encloses(outer.quad, inner.quad, thresholds=thresholds)
            ):
                surfaces.append(other)
                break
    return surfaces


def _outermost(pool: list[_Candidate], *, thresholds: DetectionThresholds) -> _Candidate:
    """The member returned as the card: the largest, with two exceptions.

    It must enclose the distinct objects in its group (#340): those well inside
    it by area, not another pass's copy of the same edge.

    And it gives way to a copy of the same card that reads squarer by at least
    :attr:`DetectionThresholds.same_card_max_skew_gap` (#339): the keystone is
    then whatever the larger merged in — on dark cloth, a spur of weave — not
    the card's own perspective.

    "Largest" is the area of the corners it would return (#360), not the
    recorded area: a fallback candidate records its admission rectangle's
    (#324), which on a gold front beat the card's own edge while its corners
    sat inside it.
    """
    enclosing = [
        member
        for member in pool
        if all(
            _encloses(member.quad, other.quad, thresholds=thresholds)
            for other in pool
            if other.area <= thresholds.case_max_area_ratio * member.area
        )
    ]
    candidates = enclosing or pool
    largest = max(candidates, key=lambda member: _area(member.quad))
    skew = _opposite_side_ratio(largest.quad)
    squarer = [
        member
        for member in candidates
        if member.area > thresholds.case_max_area_ratio * largest.area
        and skew - _opposite_side_ratio(member.quad) >= thresholds.same_card_max_skew_gap
    ]
    return max(squarer or [largest], key=lambda member: _area(member.quad))


def _encloses(outer: _Quad, inner: _Quad, *, thresholds: DetectionThresholds) -> bool:
    """Whether every corner of `inner` lies inside `outer`, give or take the slack."""
    boundary = np.array(outer, dtype=np.float32)
    return all(
        cv2.pointPolygonTest(boundary, corner, measureDist=True) >= -thresholds.containment_slack_px
        for corner in inner
    )


def _overlaps(outer: _Candidate, inner: _Candidate, *, thresholds: DetectionThresholds) -> bool:
    """#335's overlap containment: a card-shaped container holding nine-tenths."""
    return _quad_aspect(outer.quad) >= thresholds.case_max_aspect and (
        _overlap(inner.quad, np.array(outer.quad, dtype=np.float32))
        >= thresholds.containment_min_overlap
    )


def _overlap(inner: _Quad, boundary: MatLike) -> float:
    """The share of `inner`'s area lying inside the convex `boundary`."""
    shared, _polygon = cv2.intersectConvexConvex(np.array(inner, dtype=np.float32), boundary)
    return float(shared) / max(_area(inner), 1e-9)


def _is_a_case(
    outer: _Candidate, inner: list[_Candidate], *, thresholds: DetectionThresholds
) -> bool:
    """Whether a containing quadrilateral is a case around the card it contains.

    Both halves are needed. The container must not be card-shaped — below
    :attr:`DetectionThresholds.case_max_aspect` — and what it contains must be
    closer to a card's proportions than it is. The second is what keeps the
    rule off #206's phantoms, where the container *is* the card and the thing
    inside it is an artwork window.

    It does not keep the rule off a tilted bare card (#325): on real tilts the
    card's text region foreshortened *less* than the card, by under 0.02, and
    the comparison was decided by noise. So neither half is read through a
    keystone past :attr:`DetectionThresholds.case_max_perspective_ratio`, which
    is the gate's unusable line — such a photograph is refused on perspective
    whatever this answers.
    """
    if _opposite_side_ratio(outer.quad) > thresholds.case_max_perspective_ratio:
        return False
    contained = max(inner, key=lambda member: member.area)
    return outer.aspect < thresholds.case_max_aspect and abs(contained.aspect - CARD_ASPECT) < abs(
        outer.aspect - CARD_ASPECT
    )


def _holds_a_card(
    card: _Candidate, group: list[_Candidate], *, thresholds: DetectionThresholds
) -> bool:
    """Whether the returned quadrilateral is a case holding a card in its own group.

    #320's two halves again, read inside one group: not card-shaped itself, and
    holding a member nearer a card's proportions. The member must also be
    well inside by area (:attr:`DetectionThresholds.case_max_area_ratio`),
    because in-group members are mostly the same card found by another pass,
    and bare cards measured 0.966 and up. Not #320's forbidden span signal,
    which compared members with each other rather than with the returned one.

    Aspects are read from the corners, not the admission rectangle: for a
    fallback candidate the two disagree (#324), and on real angled slabs the
    rectangle read 0.656 and 0.701 where the keystone read 0.509 and 0.495.
    """
    aspect = _quad_aspect(card.quad)
    if aspect >= thresholds.case_max_aspect or (
        _opposite_side_ratio(card.quad) > thresholds.case_max_perspective_ratio
    ):
        return False
    return any(
        member.area <= thresholds.case_max_area_ratio * card.area
        and abs(_quad_aspect(member.quad) - CARD_ASPECT) < abs(aspect - CARD_ASPECT)
        for member in group
        if member is not card
    )


def _saturation_union(
    card: _Candidate, candidates: list[_Candidate], *, thresholds: DetectionThresholds
) -> bool:
    """Whether the returned quadrilateral is two cards only the saturation pass joined.

    #345: on a card-shaped union of two overlapping saturated backs, every
    luminance pass traced the upper card and only the saturation pass's wear
    closing traced the union, which #342's shape line does not reach. So: read
    square-on, every copy of the returned quadrilateral (another pass tracing
    the same outline) came from the saturation pass, and it holds something a
    luminance pass traced — well inside by area, square-on, and lying along it.

    Along it, not across: a card the saturation pass alone finds can hold a
    bright artwork window, which lies across the card. Over 150 real
    photographs the returned quadrilateral was saturation-only on the
    Steelix/Garganacl pairs and one corpus front with nothing inside it.

    ponytail: two real positives from one scene. A union a luminance pass also
    traces, or a pair of unsaturated backs, is not reached; a learned detector
    that counts cards is the upgrade.
    """
    if _opposite_side_ratio(card.quad) > thresholds.case_square_on_max_perspective_ratio:
        return False
    boundary = np.array(card.quad, dtype=np.float32)
    copies = [
        other
        for other in candidates
        if other.area > thresholds.case_max_area_ratio * card.area
        and card.area > thresholds.case_max_area_ratio * other.area
        and _overlap(other.quad, boundary) >= thresholds.containment_min_overlap
    ]
    if not all(copy.saturation for copy in [card, *copies]):
        return False
    axis = _long_axis_degrees(card.quad)
    return any(
        not inner.saturation
        and inner.area <= thresholds.case_max_area_ratio * card.area
        and (
            _encloses(card.quad, inner.quad, thresholds=thresholds)
            or _overlap(inner.quad, boundary) >= thresholds.containment_min_overlap
        )
        and _opposite_side_ratio(inner.quad) <= thresholds.case_square_on_max_perspective_ratio
        and abs((_long_axis_degrees(inner.quad) - axis + 90.0) % 180.0 - 90.0)
        <= _PARALLEL_MAX_DEGREES
        for inner in candidates
    )


def _border_outside(quad: _Quad, saturation: _Gray, *, thresholds: DetectionThresholds) -> bool:
    """Whether a saturated border runs round the quadrilateral, with the surface beyond (#341).

    A dark-blue border on a dim surface is the surface's own grey, so every
    luminance pass traces the light panel inside it, and the saturation pass,
    whose map is noise at that value, never closes the card. The panel's own
    shape says nothing: square-on its aspect is 0.03 from a card's, and a tilt
    erases that. Outside it, the border is still saturated, and the surface
    beyond it is not.

    Per side, not an average: a tilt foreshortens the far border below the near
    line, and a glare-lit side loses its saturation.
    """
    blurred = cv2.GaussianBlur(saturation, (5, 5), 0)
    sides = 0
    for index in range(4):
        near = _band_level(quad, index, _BAND_NEAR, blurred)
        far = _band_level(quad, index, _BAND_FAR, blurred)
        if near is None or far is None:
            continue
        if (
            near >= thresholds.border_band_min_saturation
            and near - far >= thresholds.border_band_min_step
        ):
            sides += 1
    return sides >= thresholds.border_band_min_sides


def _band_level(
    quad: _Quad, index: int, fractions: tuple[float, ...], image: _Gray
) -> float | None:
    """`image` along side `index` moved outward, averaged over `fractions` of the size across it.

    Each line is sampled like :func:`_edge_support`, over the side's middle 80%,
    and only where it lies inside the frame. `None` when no line has a sample.
    """
    first, second = quad[index], quad[(index + 1) % 4]
    third, fourth = quad[(index + 2) % 4], quad[(index + 3) % 4]
    across = math.hypot(
        (first[0] + second[0] - third[0] - fourth[0]) / 2.0,
        (first[1] + second[1] - third[1] - fourth[1]) / 2.0,
    )
    normal_x, normal_y = second[1] - first[1], first[0] - second[0]
    length = math.hypot(normal_x, normal_y)
    if length <= 0.0:
        return None
    normal_x, normal_y = normal_x / length, normal_y / length
    centre_x, centre_y = _centre(quad)
    midpoint_x, midpoint_y = (first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0
    if (midpoint_x - centre_x) * normal_x + (midpoint_y - centre_y) * normal_y < 0.0:
        normal_x, normal_y = -normal_x, -normal_y
    height, width = image.shape[:2]
    levels: list[float] = []
    for fraction in fractions:
        start = (first[0] + normal_x * fraction * across, first[1] + normal_y * fraction * across)
        end = (second[0] + normal_x * fraction * across, second[1] + normal_y * fraction * across)
        values = [
            float(image[row, column])
            for step in np.linspace(0.1, 0.9, _SIDE_SAMPLES)
            if 0 <= (column := round(start[0] + (end[0] - start[0]) * step)) < width
            and 0 <= (row := round(start[1] + (end[1] - start[1]) * step)) < height
        ]
        if values:
            levels.append(float(np.mean(values)))
    return float(np.mean(levels)) if levels else None


def _long_axis_degrees(quad: _Quad) -> float:
    """The direction of a quadrilateral's longer pair of sides, in `[0, 180)`."""
    top, right, bottom, left = _side_lengths(quad)
    start = 0 if top + bottom >= right + left else 1
    (x0, y0), (x1, y1) = quad[start], quad[start + 1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _quad_aspect(quad: _Quad) -> float:
    sides = _side_lengths(quad)
    return min(sides) / max(sides)


def _opposite_side_ratio(quad: _Quad) -> float:
    """`CardGeometry.opposite_side_ratio`, on a working-copy quadrilateral."""
    top, right, bottom, left = _side_lengths(quad)
    return max(max(top, bottom) / min(top, bottom), max(left, right) / min(left, right))


def _enclosing_ratio(group: list[_Candidate], *, thresholds: DetectionThresholds) -> float:
    """How much bigger the outermost concentric quadrilateral is — §19's sleeve.

    `1.0` means nothing plausibly enclosing was found, which is the answer for a
    bare card.

    **The standoff is measured on all four sides, on the quadrilaterals
    themselves.** Until #207 it was derived from the two areas — half the
    difference of the side lengths of two squares of those areas — which is an
    *average* standoff, and an average cannot tell a holder from a shadow. A
    holder surrounds the card; a shadow falls on the side away from the light.
    Over the corpus's 28 real photographs the enclosing quadrilateral's four
    standoffs ran as unevenly as 0.0 / 6.0 / 4.4 / -0.1 mm — one side flush
    with the card and one corner *outside* the enclosing quad altogether — and
    the average of that cleared the old floor comfortably. Requiring every side
    is what makes "encloses the card" mean what it says.

    The floor itself is a fraction of the card rather than a pixel count, for
    the same issue's second finding: what it must exclude is the six extraction
    passes disagreeing about where this card's edge is, by up to 3.0 mm, which
    scales with the card. See
    :attr:`DetectionThresholds.sleeve_standoff_fraction` for the measurement.

    ponytail: still the weakest heuristic in this package, and now an explicitly
    coarse one — it resolves a rigid holder and nothing tighter, because
    anything tighter is inside the detector's own uncertainty about the card's
    boundary. It costs a `poor`, which is "continue but tell the user", never a
    refusal, so being coarse is the safe direction. M7's segmentation model is
    the upgrade, and it is what would make a sleeve answerable at all.
    """
    outer = max(group, key=lambda member: member.area)
    inner = min(group, key=lambda member: member.area)
    if inner.area <= 0.0 or inner is outer:
        return 1.0
    ratio = outer.area / inner.area
    if ratio > thresholds.sleeve_max_ratio:
        return 1.0
    # Against the outer quadrilateral's long edge, because that is the boundary
    # this function was handed and the one `detect` returns; the two differ by
    # the standoff itself, which is under 4% by the time the floor is in play.
    floor = thresholds.sleeve_standoff_fraction * max(_side_lengths(outer.quad))
    if any(_standoff(outer.quad, inner.quad, side) < floor for side in range(4)):
        return 1.0
    return ratio


def _standoff(enclosing: _Quad, enclosed: _Quad, side: int) -> float:
    """How far the enclosing quadrilateral clears one side of the enclosed one.

    The side's midpoint rather than its corners: a corner distance is inflated
    by the diagonal, and inflated most for the shape this exists to refuse — a
    quadrilateral flush along two sides and clear on the other two still has
    two corners comfortably inside the enclosing one. Negative when that side
    lies outside, which is not a "small" standoff but a different fact, and the
    caller wants both refused.
    """
    first = enclosed[side]
    second = enclosed[(side + 1) % 4]
    midpoint = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    return float(
        cv2.pointPolygonTest(np.array(enclosing, dtype=np.float32), midpoint, measureDist=True)
    )


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
