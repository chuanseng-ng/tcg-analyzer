"""Card boundary detection — spec §18, issue #37.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The photographs are **built in the test**, on the rule
`ml/image-quality/tests/test_image_quality.py` and
`services/api/tests/test_image_validation.py` both state: a binary blob in the
repository is one nobody can read at review time, and a test asserting that a
dark card on a dark table is found is only convincing if the reader can watch it
being placed there.

Built with OpenCV rather than Pillow, so this package's tests need nothing this
package does not already depend on. PNG unless the point is the JPEG decoder.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.card_geometry import CardGeometry
from tcg_domain.confidence import InsufficientInformation
from tcg_ml_card_detection import CARD_DETECTION_VERSION, DEFAULT_DETECTION_THRESHOLDS, detect

#: Portrait, and large enough that the working downscale is a real one.
HEIGHT, WIDTH = 1600, 1200

#: A card at roughly the 63:88 proportions of the real thing, comfortably inside
#: the frame. Every positional assertion below is against these four numbers.
CARD = (285, 360, 630, 880)  # left, top, width, height


def background(tone: int = 40, seed: int = 0) -> NDArray[np.uint8]:
    """A surface with a little grain, so nothing is a perfectly flat fill.

    A flat fill is not a photograph, and a detector tuned against one would be
    tuned against a case that never arrives.
    """
    rng = np.random.default_rng(seed)
    speckle = rng.integers(-8, 9, size=(HEIGHT, WIDTH, 1))
    return np.clip(np.full((HEIGHT, WIDTH, 3), tone, np.int16) + speckle, 0, 255).astype(np.uint8)


def printed(width: int, height: int, tone: int) -> NDArray[np.uint8]:
    """A card face: a tone with printing on it rather than a plain rectangle."""
    face = np.full((height, width, 3), tone, np.uint8)
    rows, columns = np.mgrid[0:height, 0:width]
    ink = (((rows // 20) + (columns // 20)) % 2).astype(bool)
    face[ink] = max(0, tone - 40)
    return face


def photograph(
    *,
    card: tuple[int, int, int, int] = CARD,
    surface: int = 40,
    face: int = 210,
    seed: int = 0,
) -> NDArray[np.uint8]:
    picture = background(surface, seed)
    return place(picture, card, face)


def place(
    picture: NDArray[np.uint8], card: tuple[int, int, int, int], face: int
) -> NDArray[np.uint8]:
    left, top, width, height = card
    picture[top : top + height, left : left + width] = printed(width, height, face)
    return picture


def png(picture: NDArray[np.uint8]) -> bytes:
    return bytes(cv2.imencode(".png", picture)[1].tobytes())


def located(data: bytes) -> CardGeometry:
    """The geometry, or a failure that names what the detector actually said."""
    found = detect(data)
    assert isinstance(found, CardGeometry), found
    return found


# ---------------------------------------------------------------------------
# Finding the card
# ---------------------------------------------------------------------------


def test_a_card_on_a_contrasting_surface_is_found_where_it_is() -> None:
    left, top, width, height = CARD
    found = located(png(photograph()))

    corners = found.corners
    assert corners[0] == pytest.approx((left, top), abs=8)
    assert corners[1] == pytest.approx((left + width, top), abs=8)
    assert corners[2] == pytest.approx((left + width, top + height), abs=8)
    assert corners[3] == pytest.approx((left, top + height), abs=8)


def test_the_frame_is_reported_in_the_originals_coordinates_not_the_working_copys() -> None:
    """Everything downstream crops and warps the original."""
    found = located(png(photograph()))

    assert (found.frame_width, found.frame_height) == (WIDTH, HEIGHT)
    assert found.detector == CARD_DETECTION_VERSION
    assert found.thresholds == DEFAULT_DETECTION_THRESHOLDS.as_record()


def test_a_dark_card_on_a_dark_surface_is_found() -> None:
    """The issue names this case: the boundary gradient is almost nothing."""
    found = located(png(photograph(surface=30, face=62)))

    assert found.area_fraction == pytest.approx(0.288, abs=0.02)
    assert found.candidates == 1


def test_a_card_back_is_found() -> None:
    """A back is one flat field with none of a front's internal structure.

    Built as a plain fill rather than with `printed`, deliberately: if the
    detector needed the printing to find the boundary it would work on fronts
    and fail on the half of every analysis that is a back.
    """
    picture = background(150)
    left, top, width, height = CARD
    picture[top : top + height, left : left + width] = (110, 60, 35)

    assert located(png(picture)).area_fraction == pytest.approx(0.288, abs=0.02)


def test_a_close_up_card_whose_shadow_merges_with_it_is_found_where_it_is() -> None:
    """#176's first failure: the card's own shadow, merged into one grey blob.

    Photographed close up on a near-white surface, the card's soft shadow is
    darker than the surface and the grayscale Otsu split lumps card and shadow
    into one region whose fitted quadrilateral runs to the frame's own corner —
    which the detector then reported confidently. The card must be found where
    it is, not where its shadow ends.
    """
    picture = background(235)
    left, top, width, height = 140, 90, 920, 1285  # 920/1285 is a card's 63:88
    picture[top : top + height, left : left + width] = (150, 80, 40)  # saturated blue
    # The shadow: grey bands off the right and bottom edges, running to a few
    # pixels short of the frame corner, dark enough that a grayscale threshold
    # cannot separate them from the card.
    picture[top + 40 : 1597, left + width : 1197] = (130, 130, 130)
    picture[top + height : 1597, left + 40 : 1197] = (130, 130, 130)
    found = located(png(picture))

    corners = found.corners
    assert corners[0] == pytest.approx((left, top), abs=10)
    assert corners[2] == pytest.approx((left + width, top + height), abs=10)


def test_a_worn_front_with_no_luminance_contrast_is_found_by_its_colour() -> None:
    """#176's second failure: a worn front the grayscale passes cannot see.

    Wear takes the luminance contrast with it — the session's heavily worn
    front was simply not found. Its synthetic twin is an isoluminant card: a
    saturated face whose grayscale equals the surface tone, invisible to Canny
    and to a grayscale Otsu split alike, and plainly there in colour.
    """
    picture = background(117)
    left, top, width, height = CARD
    picture[top : top + height, left : left + width] = (40, 140, 100)  # gray ~= 117
    found = located(png(picture))

    assert found.corners[0] == pytest.approx((left, top), abs=8)
    assert found.area_fraction == pytest.approx(0.288, abs=0.02)


def test_a_shadow_blob_below_the_fill_threshold_loses_to_the_clear_card_beside_it() -> None:
    """#192's first failure: the merged blob is too small to be refused and wins.

    #176's refusal needs the blob to fill 70% of the frame. The corpus's back
    photographs produced the same card-plus-shadow blob at ~60% fill: the card
    with its far corner dragged to the frame's own corner by a shadow lobe. It
    touches the boundary, survives the hugging filter on area, shares the true
    card's centre-group and outvotes it as largest member — and the warp of
    that quadrilateral shows the card tilted with background at the corners. A
    member that touches the frame boundary must lose to one that is clear of
    it, however large it is.
    """
    picture = background(235)
    left, top, width, height = 300, 380, 780, 1090  # the far corner near the frame's
    right, bottom = left + width, top + height
    # The shadow: a lobe off the card's bottom-right, reaching 1 px short of
    # the frame corner (the real corner sat 4 px inside a 3024-px frame).
    lobe = np.array(
        [(right - 40, bottom - 500), (WIDTH - 1, HEIGHT - 1), (right - 500, bottom - 40)]
    )
    cv2.fillConvexPoly(picture, lobe, (130, 130, 130))
    picture[top:bottom, left:right] = (150, 80, 40)  # saturated blue
    found = located(png(picture))

    corners = found.corners
    assert corners[0] == pytest.approx((left, top), abs=10)
    assert corners[2] == pytest.approx((right, bottom), abs=10)


def test_a_worn_back_whose_chroma_ring_is_broken_at_the_corners_is_found() -> None:
    """#192's second failure: wear breaks the saturation map into fragments.

    The corpus's heavily worn back is visible only in chroma — the swirl is
    isoluminant with the white table — and it is *mottled*: whitened corners
    sever the blue border ring outright, and the interior is blue patches, not
    a field. Thresholded, that is a cloud of fragments no 3x3 closing joins,
    so no map produced a candidate and the only quadrilateral left was the
    card-plus-shadow blob. A closing kernel sized for wear (9x9 at the working
    scale) merges the fragments into one card-shaped region.
    """
    picture = background(235)
    left, top, width, height = CARD
    picture[top : top + height, left : left + width] = (235, 235, 235)
    # The border: a 45-px ring, isoluminant with the surface (gray ~235, the
    # worn-front test's trick) and saturated — chroma is all there is.
    ring = 45
    blue = (170, 250, 232)
    picture[top : top + ring, left : left + width] = blue
    picture[top + height - ring : top + height, left : left + width] = blue
    picture[top : top + height, left : left + ring] = blue
    picture[top : top + height, left + width - ring : left + width] = blue
    # The interior mottle: blue patches on a 20-px pitch, gaps a 3x3 closing
    # cannot join at the working scale and a 9x9 can.
    for row in range(top + ring + 4, top + height - ring - 12, 20):
        for column in range(left + ring + 4, left + width - ring - 12, 20):
            picture[row : row + 12, column : column + 12] = blue
    # The wear: each corner of the ring severed outright — 55 px, wider than
    # the ring itself, as a whitened corner is.
    notch = 55
    for corner_top, corner_left in (
        (top, left),
        (top, left + width - notch),
        (top + height - notch, left),
        (top + height - notch, left + width - notch),
    ):
        picture[corner_top : corner_top + notch, corner_left : corner_left + notch] = (
            235,
            235,
            235,
        )
    found = located(png(picture))

    centre_x = sum(x for x, _y in found.corners) / 4
    centre_y = sum(y for _x, y in found.corners) / 4
    assert centre_x == pytest.approx(left + width / 2, abs=20)
    assert centre_y == pytest.approx(top + height / 2, abs=20)
    assert found.area_fraction == pytest.approx(0.26, abs=0.04)


def test_a_light_bordered_card_on_a_light_table_is_found_by_its_shadow() -> None:
    """#193: the auto-Canny thresholds sit above a soft drop shadow's gradient.

    The corpus's silver-bordered card on a white table produced no candidate on
    any map — the median of a mostly-white frame puts both Canny levels far
    above the card edge's gradient, both Otsu polarities split the card's
    interior from its border rather than the card from the table, and the
    border is as unsaturated as the table. What remains is what a physical card
    always has: a drop shadow, a few tones darker, hugging the edge. The fixed
    low-threshold pass exists for exactly this photograph.
    """
    picture = background(235)
    left, top, width, height = CARD
    # A soft shadow ring: 6 px around the card, a little darker than the table.
    cv2.rectangle(
        picture,
        (left - 6, top - 6),
        (left + width + 6, top + height + 6),
        (215, 215, 215),
        thickness=6,
    )
    # The face: barely brighter than the table, inside the speckle amplitude,
    # so no tonal split can separate them — and unsaturated, so the chroma
    # pass sees nothing either. The artwork window: the one strong rectangle,
    # which must not win.
    picture[top : top + height, left : left + width] = (238, 238, 238)
    art_left, art_top = left + 60, top + 70
    picture[art_top : art_top + 480, art_left : art_left + 510] = (90, 110, 60)
    found = located(png(picture))

    corners = found.corners
    assert corners[0] == pytest.approx((left - 6, top - 6), abs=14)
    assert corners[2] == pytest.approx((left + width + 6, top + height + 6), abs=14)
    assert found.candidates == 1


def test_a_jpeg_is_read_as_readily_as_a_png() -> None:
    encoded = cv2.imencode(".jpg", photograph(), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()

    assert located(bytes(encoded)).area_fraction == pytest.approx(0.288, abs=0.02)


def test_the_same_photograph_twice_gives_the_same_answer() -> None:
    """No sampling, no randomness — a verdict a rerun could change is not one."""
    data = png(photograph())

    assert detect(data) == detect(data)


# ---------------------------------------------------------------------------
# Corner ordering
# ---------------------------------------------------------------------------


def rotated(degrees: float) -> bytes:
    picture = photograph()
    turn = cv2.getRotationMatrix2D((WIDTH / 2, HEIGHT / 2), degrees, 1.0)
    return png(cv2.warpAffine(picture, turn, (WIDTH, HEIGHT), borderValue=(40, 40, 40)))


@pytest.mark.parametrize("degrees", [-10.0, 0.0, 10.0])
def test_the_corners_run_clockwise_from_the_top_left_whatever_the_orientation(
    degrees: float,
) -> None:
    """Constructing the geometry at all is the assertion.

    `CardGeometry` refuses a quadrilateral whose signed area is not positive, so
    an order that had drifted would raise rather than produce a mirrored card.
    What is checked here is the *phase*: the first corner is the one nearest the
    frame's origin.
    """
    found = located(rotated(degrees))

    nearest = min(range(4), key=lambda index: sum(found.corners[index]))
    assert nearest == 0


def test_a_landscape_card_is_ordered_the_same_way() -> None:
    left, top, height, width = CARD  # the card on its side
    found = located(png(photograph(card=(top - 200, left + 125, width, height))))

    nearest = min(range(4), key=lambda index: sum(found.corners[index]))
    assert nearest == 0
    top_edge, right_edge, _bottom, _left = found.side_lengths
    assert top_edge > right_edge


# ---------------------------------------------------------------------------
# The cases that must be reported rather than guessed
# ---------------------------------------------------------------------------


def test_a_photograph_with_no_card_in_it_says_so() -> None:
    """#37's acceptance criterion: a failure degrades into the quality gate.

    An empty frame is itself a rectangle of card-like proportions, which is
    exactly the wrong answer to be confident about.
    """
    found = detect(png(background()))

    assert isinstance(found, InsufficientInformation)
    assert found.reason and "no card" in found.reason


def test_bytes_that_do_not_decode_are_answered_rather_than_raised() -> None:
    """The gate is the one place undecodable bytes become a job failure."""
    found = detect(b"this is not an image")

    assert isinstance(found, InsufficientInformation)
    assert found.reason and "decode" in found.reason


def test_a_frame_filling_blob_is_refused_rather_than_returned_confidently() -> None:
    """#176's scope: never a confident frame-corner quadrilateral.

    One dark region running to a few pixels of every frame edge is the
    picture's own boundary wearing card-like proportions — the shape the
    shadow-merged close-ups produced. With nothing else card-like in the frame
    the honest answer is a refusal, not an 82%-confident mis-frame.
    """
    picture = background(235)
    picture[4:1524, 4:1124] = (60, 60, 60)
    found = detect(png(picture))

    assert isinstance(found, InsufficientInformation)
    assert found.reason and "frame" in found.reason


def test_two_cards_are_counted_as_two() -> None:
    picture = background()
    place(picture, (60, 360, 500, 700), 210)
    place(picture, (640, 360, 500, 700), 210)

    assert located(png(picture)).candidates == 2


def test_an_artwork_window_inside_the_card_is_not_a_second_card() -> None:
    """#206's first failure shape: the artwork window survives as a phantom.

    On four of the corpus's 28 fronts the artwork window — convex, card-aspect,
    well filled — passed every candidate filter, and its centre sits far enough
    from the card's at close range to escape the concentric grouping. The count
    said two cards and the gate refused the photograph for `multiple_cards`,
    with one card in the frame, always. A quadrilateral wholly inside another
    card-like quadrilateral is that card's own structure, never a second card.
    """
    picture = photograph()
    left, top, _width, _height = CARD
    art_left, art_top = left + 60, top + 70
    picture[art_top : art_top + 480, art_left : art_left + 510] = (90, 110, 60)
    found = located(png(picture))

    assert found.candidates == 1
    assert found.enclosing_ratio == 1.0
    assert found.area_fraction == pytest.approx(0.288, abs=0.02)


def test_a_panel_sharing_the_cards_own_edges_is_not_a_second_card_either() -> None:
    """#206's second failure shape: the phantom pokes a few pixels outside.

    The corpus's fourth phantom was the card's lower text panel, spanning the
    card's full width — a contour that partly reuses the card's own boundary,
    which fitting jitter then places a couple of pixels *outside* the winning
    quadrilateral (2.8 px at the working scale, measured). Containment must
    tolerate that, or exactly the shape that shares the card's edges — the one
    most obviously not a second card — is the one still counted as two.
    """
    picture = background()
    left, top, width, height = CARD
    place(picture, CARD, 210)
    # The panel: flush with the card's own left, right and bottom edges, and
    # saturated where card and surface are not, so the saturation pass yields
    # its quadrilateral alone — while a bright enough tone keeps the whole
    # card one Otsu region, so the winning quadrilateral stays the card's.
    # Where panel edge and card edge coincide, the fitted walls land a pixel
    # or two either side of the winner's.
    picture[top + height // 2 : top + height, left : left + width] = (60, 90, 230)
    found = located(png(picture))

    assert found.candidates == 1
    assert found.area_fraction == pytest.approx(0.288, abs=0.02)


def test_a_card_found_by_more_than_one_pass_is_still_one_card() -> None:
    """Three extraction passes and a closed edge ribbon each yield a contour.

    Counting those separately would report `multiple_cards` on every photograph,
    which the gate refuses an analysis for.
    """
    assert located(png(photograph())).candidates == 1


def test_a_clipped_card_reports_no_margin_rather_than_inventing_the_missing_part() -> None:
    _left, _top, width, height = CARD
    picture = place(background(), (0, 0, width, height), 210)

    assert located(png(picture)).border_margin_fraction == 0.0


# ---------------------------------------------------------------------------
# Sleeves
# ---------------------------------------------------------------------------


def test_a_bare_card_reports_nothing_enclosing_it() -> None:
    assert located(png(photograph())).enclosing_ratio == 1.0


def test_a_card_inside_a_holder_reports_the_holder() -> None:
    """One card, not two — and the spread within it is the holder.

    A 6 mm standoff, which at this file's 10 px/mm is a top-loader. It was a
    2 mm sleeve until #207 measured what the extraction passes disagree by on
    real photographs — up to 3 mm, which is more than a sleeve that thin stands
    off — and a fixture below the detector's own resolution asserts nothing.
    """
    picture = background()
    place(picture, (225, 300, 750, 1000), 120)
    place(picture, CARD, 215)
    found = located(png(picture))

    assert found.candidates == 1
    assert 1.0 < found.enclosing_ratio <= DEFAULT_DETECTION_THRESHOLDS.sleeve_max_ratio


def test_a_holder_too_narrow_to_resolve_is_not_reported() -> None:
    """#207's resolution limit, stated as a test.

    A 2 mm standoff on every side. On the corpus's 28 real photographs the six
    extraction passes placed the card's own boundary up to 3 mm apart from each
    other, so a quadrilateral this close is not distinguishable from another
    pass's opinion of the same edge — and answering "sleeved" from it is the
    confidently-wrong output spec §2.7 forbids. Reporting nothing is the honest
    answer, and the condition can only warn, never refuse, so nothing is lost
    but a warning that was wrong three times in four.
    """
    picture = background()
    place(picture, (265, 340, 670, 920), 120)
    place(picture, CARD, 215)

    assert located(png(picture)).enclosing_ratio == 1.0


def test_a_quadrilateral_that_stands_off_on_two_sides_only_is_not_a_holder() -> None:
    """#207's measured shape: a drop shadow, not a holder.

    A holder surrounds the card; a shadow falls on the side away from the light.
    On the corpus the enclosing quadrilateral's four per-side standoffs ran as
    unevenly as 0.0 / 6.0 / 4.4 / -0.1 mm — one side flush with the card and one
    corner *outside* the enclosing quad altogether — because the passes that
    found it were reading the card's shadow, its Otsu region or its saturation
    map rather than a second object. Averaging those four into one number, which
    is what an area ratio does, reads the shadow as a sleeve.
    """
    picture = background()
    left, top, width, height = CARD
    # Flush with the card's left and top edges, 4 mm clear of the other two.
    place(picture, (left, top, width + 40, height + 40), 120)
    place(picture, CARD, 215)

    assert located(png(picture)).enclosing_ratio == 1.0


def test_something_far_larger_than_the_card_is_not_a_sleeve() -> None:
    """A mat, a mount or a table edge. The band has an upper bound for this."""
    picture = background()
    place(picture, (150, 150, 900, 1300), 120)
    place(picture, CARD, 215)

    assert located(png(picture)).enclosing_ratio == 1.0


# ---------------------------------------------------------------------------
# Confidence and thresholds
# ---------------------------------------------------------------------------


def test_the_confidence_is_a_fraction_and_a_clean_card_scores_well() -> None:
    found = located(png(photograph()))

    assert 0.0 <= found.confidence.value <= 1.0
    assert not found.confidence.is_below(0.8)


def test_a_card_against_the_frame_boundary_scores_below_a_clear_one() -> None:
    """#176's scope: a corner on the frame boundary costs confidence.

    A clipped card is still returned — the gate is what tells the user part of
    it is missing — but a boundary it shares with the picture is a boundary the
    detector cannot vouch for.
    """
    _left, _top, width, height = CARD
    clipped = located(png(place(background(), (0, 0, width, height), 210)))
    clear = located(png(photograph()))

    assert clipped.confidence.value < clear.confidence.value


def test_the_thresholds_are_a_parameter() -> None:
    """A caller that wants different numbers passes different numbers."""
    from tcg_ml_card_detection import DetectionThresholds

    impossible = DetectionThresholds(min_area_fraction=0.9, max_area_fraction=0.95)

    assert isinstance(detect(png(photograph()), thresholds=impossible), InsufficientInformation)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("work_long_edge", 0, "work_long_edge"),
        ("min_rectangularity", 1.5, "min_rectangularity"),
        ("approx_epsilon", 0.0, "approx_epsilon"),
        ("sleeve_standoff_fraction", 0.0, "sleeve_standoff_fraction"),
        ("sleeve_standoff_fraction", 1.0, "sleeve_standoff_fraction"),
        ("containment_slack_px", -1.0, "containment_slack_px"),
        ("max_aspect", 0.1, "aspect band"),
        ("frame_margin_fraction", 0.0, "frame_margin_fraction"),
        ("frame_fill_fraction", 1.5, "frame_fill_fraction"),
    ],
)
def test_a_threshold_that_does_not_make_sense_is_refused(
    field: str, value: float, message: str
) -> None:
    from tcg_ml_card_detection import DetectionThresholds

    with pytest.raises(ValueError, match=message):
        DetectionThresholds(**{field: value})  # type: ignore[arg-type]
