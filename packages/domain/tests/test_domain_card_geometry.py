"""A detected card boundary — spec §18, issue #37.

Pure: no OpenCV, no numpy, no photograph. Everything here is either a rule about
what a quadrilateral may be or arithmetic over four points, and both are things
`ml/card-detection` and `ml/image-quality` have to agree on without either being
able to see the other.

The measurements are checked against fixtures worked out by hand rather than
against the same formula written twice, which would only assert that the code
equals itself.
"""

from __future__ import annotations

import pytest
from tcg_domain.card_geometry import CORNER_NAMES, CardGeometry
from tcg_domain.confidence import Confidence
from tcg_domain.errors import InvalidCardGeometry

DETECTOR = "card-detection-test-v0"

#: A 300 x 400 rectangle sitting 100 px inside a 500 x 600 frame, clockwise from
#: the top left. Every number below is derived from these four points on paper.
SQUARE_ON = ((100.0, 100.0), (400.0, 100.0), (400.0, 500.0), (100.0, 500.0))
FRAME = {"frame_width": 500, "frame_height": 600}


def geometry(
    corners: tuple[tuple[float, float], ...] = SQUARE_ON, **overrides: object
) -> CardGeometry:
    fields: dict[str, object] = {
        "corners": corners,
        "confidence": Confidence.of(0.9),
        "detector": DETECTOR,
        **FRAME,
    }
    fields.update(overrides)
    return CardGeometry(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# What a boundary may be
# ---------------------------------------------------------------------------


def test_the_corner_order_is_the_one_perspective_correction_reads() -> None:
    assert CORNER_NAMES == ("top_left", "top_right", "bottom_right", "bottom_left")


def test_a_boundary_has_four_corners() -> None:
    with pytest.raises(InvalidCardGeometry, match="four corners"):
        geometry(SQUARE_ON[:3])


def test_a_corner_is_a_pair_of_finite_numbers() -> None:
    with pytest.raises(InvalidCardGeometry, match=r"must be an \(x, y\) pair"):
        geometry((*SQUARE_ON[:3], (1.0, 2.0, 3.0)))  # type: ignore[arg-type]
    with pytest.raises(InvalidCardGeometry, match="must be finite"):
        geometry((*SQUARE_ON[:3], (float("inf"), 500.0)))
    with pytest.raises(InvalidCardGeometry, match="must be a real number"):
        geometry((*SQUARE_ON[:3], ("100", 500.0)))  # type: ignore[arg-type]


def test_corners_running_the_wrong_way_round_are_refused() -> None:
    """The point of the whole type.

    These four points describe the same rectangle. Traversed anticlockwise they
    are a perfectly convex quadrilateral, so a convexity check alone would let
    them through — and perspective correction would mirror the card, silently.
    """
    with pytest.raises(InvalidCardGeometry, match="clockwise"):
        geometry(tuple(reversed(SQUARE_ON)))


def test_a_concave_quadrilateral_is_refused() -> None:
    dented = (SQUARE_ON[0], SQUARE_ON[1], (250.0, 200.0), SQUARE_ON[3])
    with pytest.raises(InvalidCardGeometry, match="convex"):
        geometry(dented)


def test_rotating_the_same_cycle_is_still_clockwise() -> None:
    """Starting at a different corner is a phase error, not an order error.

    Worth stating because the validation cannot catch it: it is the *detector's*
    job to fix the phase, and `_clockwise_from_top_left` is where that happens.
    """
    rotated = (SQUARE_ON[1], SQUARE_ON[2], SQUARE_ON[3], SQUARE_ON[0])
    assert geometry(rotated).area == geometry().area


def test_the_frame_must_have_a_positive_size() -> None:
    with pytest.raises(InvalidCardGeometry, match="frame_width must be positive"):
        geometry(frame_width=0)
    with pytest.raises(InvalidCardGeometry, match="frame_height must be positive"):
        geometry(frame_height=-1)


def test_the_detector_is_recorded_and_may_not_be_a_pointer_to_whatever_is_current() -> None:
    """The refusal `catalog_version.py` and `image_quality.py` both make."""
    assert geometry().detector == DETECTOR
    with pytest.raises(InvalidCardGeometry, match="fixed version"):
        geometry(detector="card-detection-latest")
    with pytest.raises(InvalidCardGeometry, match="non-empty"):
        geometry(detector="   ")


def test_a_geometry_is_itself_a_candidate() -> None:
    with pytest.raises(InvalidCardGeometry, match="at least 1"):
        geometry(candidates=0)


def test_an_enclosing_quadrilateral_is_never_smaller_than_what_it_encloses() -> None:
    with pytest.raises(InvalidCardGeometry, match="cannot be smaller"):
        geometry(enclosing_ratio=0.95)
    assert geometry(enclosing_ratio=1.0).enclosing_ratio == 1.0


def test_the_confidence_is_a_validated_one_not_a_bare_number() -> None:
    """So that an 87 meaning 87% cannot arrive."""
    with pytest.raises(InvalidCardGeometry, match="must be a Confidence"):
        geometry(confidence=0.9)


def test_the_thresholds_are_copied_rather_than_referenced() -> None:
    """A record that changed after the fact would not be a record."""
    passed = {"card_detection_work_long_edge": 1024.0}
    subject = geometry(thresholds=passed)
    passed["card_detection_work_long_edge"] = 2048.0

    assert subject.thresholds["card_detection_work_long_edge"] == 1024.0


# ---------------------------------------------------------------------------
# The measurements spec §19's geometric five are judged from
# ---------------------------------------------------------------------------


def test_the_area_and_the_share_of_the_frame() -> None:
    """300 x 400 = 120000, in a 500 x 600 = 300000 frame."""
    assert geometry().area == pytest.approx(120_000.0)
    assert geometry().frame_area == pytest.approx(300_000.0)
    assert geometry().area_fraction == pytest.approx(0.4)


def test_the_side_lengths_are_top_right_bottom_left() -> None:
    assert geometry().side_lengths == pytest.approx((300.0, 400.0, 300.0, 400.0))


def test_a_square_on_card_has_an_opposite_side_ratio_of_one() -> None:
    assert geometry().opposite_side_ratio == pytest.approx(1.0)


def test_a_tilted_card_has_a_longer_near_edge_than_far_edge() -> None:
    """The top edge shortened to 200 against a 300 px bottom edge is a 1.5."""
    tilted = ((150.0, 100.0), (350.0, 100.0), (400.0, 500.0), (100.0, 500.0))
    assert geometry(tilted).opposite_side_ratio == pytest.approx(1.5)


def test_the_border_margin_is_the_least_gap_over_the_frames_short_edge() -> None:
    """100 px clear on the left, right and top; 100 px on the bottom too.

    The short edge is the frame's 500, so the fraction is 0.2.
    """
    assert geometry().border_margin_fraction == pytest.approx(0.2)


def test_a_card_touching_the_frame_has_no_margin_at_all() -> None:
    clipped = ((0.0, 0.0), (300.0, 0.0), (300.0, 400.0), (0.0, 400.0))
    assert geometry(clipped).border_margin_fraction == 0.0


def test_a_corner_reported_outside_the_frame_is_still_only_against_the_edge() -> None:
    """Clamped rather than negative, so the gate's ordering cannot invert."""
    overhanging = ((-20.0, -30.0), (300.0, -30.0), (300.0, 400.0), (-20.0, 400.0))
    assert geometry(overhanging).border_margin_fraction == 0.0


def test_a_geometry_says_what_it_is() -> None:
    assert "40%" in str(geometry())
    assert DETECTOR in str(geometry())
