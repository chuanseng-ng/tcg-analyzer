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


def test_two_cards_are_counted_as_two() -> None:
    picture = background()
    place(picture, (60, 360, 500, 700), 210)
    place(picture, (640, 360, 500, 700), 210)

    assert located(png(picture)).candidates == 2


def test_a_card_found_by_more_than_one_pass_is_still_one_card() -> None:
    """Three extraction passes and a closed edge ribbon each yield a contour.

    Counting those separately would report `multiple_cards` on every photograph,
    which the gate refuses an analysis for.
    """
    assert located(png(photograph())).candidates == 1


def test_a_clipped_card_reports_no_margin_rather_than_inventing_the_missing_part() -> None:
    picture = place(background(), (0, 0, *CARD[2:]), 210)

    assert located(png(picture)).border_margin_fraction == 0.0


# ---------------------------------------------------------------------------
# Sleeves
# ---------------------------------------------------------------------------


def test_a_bare_card_reports_nothing_enclosing_it() -> None:
    assert located(png(photograph())).enclosing_ratio == 1.0


def test_a_card_inside_a_sleeve_reports_the_sleeve() -> None:
    """One card, not two — and the spread within it is the sleeve."""
    picture = background()
    place(picture, (265, 340, 670, 920), 120)
    place(picture, CARD, 215)
    found = located(png(picture))

    assert found.candidates == 1
    assert 1.0 < found.enclosing_ratio <= DEFAULT_DETECTION_THRESHOLDS.sleeve_max_ratio


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
        ("sleeve_min_margin", 0.0, "sleeve_min_margin"),
        ("max_aspect", 0.1, "aspect band"),
    ],
)
def test_a_threshold_that_does_not_make_sense_is_refused(
    field: str, value: float, message: str
) -> None:
    from tcg_ml_card_detection import DetectionThresholds

    with pytest.raises(ValueError, match=message):
        DetectionThresholds(**{field: value})  # type: ignore[arg-type]
