"""Corner classification — spec §14, issue #183.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The artifacts are **built in the test**, on the rule
`ml/centering/tests/test_centering.py` restates: a binary blob in the
repository is one nobody can read at review time, and a test asserting that a
360-pixel white patch is moderate whitening is only convincing if the reader
can watch the patch being drawn 12 pixels deep and 30 wide.

The card frame is constructed by hand rather than read from a normalization
record — this package does not depend on `ml/normalization`; the frame
crosses between them as a domain type.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.annotation import CornerLabel, CornerRegion, DefectSeverity
from tcg_domain.condition import BoundingBox, RegionFinding
from tcg_domain.confidence import InsufficientInformation
from tcg_ml_corners import (
    CORNERS_VERSION,
    DEFAULT_CORNER_THRESHOLDS,
    CornerThresholds,
    classify,
)

#: The real artifact's numbers (#194): 63 x 88 mm at 12 px/mm, inside a 2 mm
#: margin of real photograph.
CARD_PX_WIDTH, CARD_PX_HEIGHT = 756, 1056
MARGIN_PX = 24
WIDTH, HEIGHT = CARD_PX_WIDTH + 2 * MARGIN_PX, CARD_PX_HEIGHT + 2 * MARGIN_PX

#: Where the card sits in the artifact, as the caller derives it from the
#: stored normalization record — fractions of the unit square.
CARD_FRAME = BoundingBox(
    x=MARGIN_PX / WIDTH,
    y=MARGIN_PX / HEIGHT,
    width=CARD_PX_WIDTH / WIDTH,
    height=CARD_PX_HEIGHT / HEIGHT,
)

#: The photographed surface in the margin, the content region's paper and ink
#: — greys, none of them near-white at the V floor.
SURFACE, PAPER, INK = 90, 140, 40

#: A saturated yellow printed border (BGR) — the classic layout, decisively
#: not near-white however bright.
BORDER_BGR = (0, 180, 220)

#: Exposed paper core at a worn corner (BGR) — near-white.
WHITE_BGR = (245, 245, 245)

#: The default detection band: depth [2, 14) from either card edge. Patches
#: drawn inside it, clear of the 30 px corner-arc tip square, land wholly in
#: the detection region, so their areas are hand-computable: 12 px deep by
#: 30 px wide is 360 px = 2.5 mm² at 144 px²/mm².
BAND_TOP, BAND_BOTTOM = 2, 14
CLEAR_OF_TIP = 30


def a_drawn_card(*, border_bgr: tuple[int, int, int] = BORDER_BGR) -> NDArray[np.uint8]:
    """An artifact: grey margin, a bordered card, a checkered content region."""
    rng = np.random.default_rng(7)
    surface = rng.integers(SURFACE - 10, SURFACE + 10, (HEIGHT, WIDTH), np.uint8)
    picture = np.stack([surface] * 3, axis=2)

    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    card[:, :] = border_bgr

    content = card[36 : CARD_PX_HEIGHT - 36, 36 : CARD_PX_WIDTH - 36]
    content[:, :] = PAPER
    rows, columns = np.mgrid[0 : content.shape[0], 0 : content.shape[1]]
    content[(((rows // 40) + (columns // 40)) % 2).astype(bool)] = INK
    return picture


def whitened_along_the_top_edge(
    picture: NDArray[np.uint8], *, columns: slice, at_the_bottom: bool = False
) -> None:
    """Paint exposed core inside the detection band along a horizontal edge."""
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    if at_the_bottom:
        card[CARD_PX_HEIGHT - BAND_BOTTOM : CARD_PX_HEIGHT - BAND_TOP, columns] = WHITE_BGR
    else:
        card[BAND_TOP:BAND_BOTTOM, columns] = WHITE_BGR


def encoded(picture: NDArray[np.uint8]) -> bytes:
    ok, data = cv2.imencode(".png", picture)
    assert ok
    return bytes(data.tobytes())


def classified(picture: NDArray[np.uint8]) -> dict[CornerRegion, RegionFinding]:
    result = classify(encoded(picture), card_frame=CARD_FRAME)
    assert not isinstance(result, InsufficientInformation)
    return dict(result)


def test_a_clean_card_reports_all_four_corners_clean() -> None:
    corners = classified(a_drawn_card())

    assert set(corners) == set(CornerRegion)
    for finding in corners.values():
        assert finding.label is CornerLabel.CLEAN
        assert finding.severity is None
        assert finding.bounding_box is None
        assert finding.confidence.value >= 0.9


def test_a_small_white_fleck_is_minor_whitening() -> None:
    """72 px inside the band is 0.5 mm² — past the noise floor, nowhere near
    moderate."""
    picture = a_drawn_card()
    whitened_along_the_top_edge(picture, columns=slice(CLEAR_OF_TIP, CLEAR_OF_TIP + 6))

    corners = classified(picture)

    assert corners[CornerRegion.TOP_LEFT].label is CornerLabel.WHITENING
    assert corners[CornerRegion.TOP_LEFT].severity is DefectSeverity.MINOR
    assert corners[CornerRegion.TOP_RIGHT].label is CornerLabel.CLEAN
    assert corners[CornerRegion.BOTTOM_LEFT].label is CornerLabel.CLEAN
    assert corners[CornerRegion.BOTTOM_RIGHT].label is CornerLabel.CLEAN


def test_a_visible_patch_is_moderate_whitening() -> None:
    """360 px is 2.5 mm² — between the 1.5 and 4.0 mm² boundaries."""
    picture = a_drawn_card()
    whitened_along_the_top_edge(picture, columns=slice(CLEAR_OF_TIP, CLEAR_OF_TIP + 30))

    corners = classified(picture)

    assert corners[CornerRegion.TOP_LEFT].label is CornerLabel.WHITENING
    assert corners[CornerRegion.TOP_LEFT].severity is DefectSeverity.MODERATE


def test_a_heavily_whitened_corner_is_severe() -> None:
    """The band whitened to the crop's far side: 54 x 12 = 648 px = 4.5 mm²."""
    picture = a_drawn_card()
    whitened_along_the_top_edge(picture, columns=slice(CLEAR_OF_TIP, 110))

    corners = classified(picture)

    assert corners[CornerRegion.TOP_LEFT].label is CornerLabel.WHITENING
    assert corners[CornerRegion.TOP_LEFT].severity is DefectSeverity.SEVERE


@pytest.mark.parametrize("region", list(CornerRegion))
def test_whitening_is_reported_at_the_corner_that_has_it(region: CornerRegion) -> None:
    """One code path serves four corners via flips; each flip is exercised."""
    picture = a_drawn_card()
    left = region in (CornerRegion.TOP_LEFT, CornerRegion.BOTTOM_LEFT)
    bottom = region in (CornerRegion.BOTTOM_LEFT, CornerRegion.BOTTOM_RIGHT)
    columns = (
        slice(CLEAR_OF_TIP, CLEAR_OF_TIP + 30)
        if left
        else slice(CARD_PX_WIDTH - CLEAR_OF_TIP - 30, CARD_PX_WIDTH - CLEAR_OF_TIP)
    )
    whitened_along_the_top_edge(picture, columns=columns, at_the_bottom=bottom)

    corners = classified(picture)

    for candidate, finding in corners.items():
        if candidate is region:
            assert finding.label is CornerLabel.WHITENING
        else:
            assert finding.label is CornerLabel.CLEAN


def test_a_white_bordered_card_answers_unknown_for_every_corner() -> None:
    """A near-white printed border makes whitening indistinguishable in this
    signal — guessing clean there is the confidently-wrong output §2.7
    forbids, so each corner refuses as `unknown`."""
    corners = classified(a_drawn_card(border_bgr=(240, 240, 240)))

    for finding in corners.values():
        assert finding.label is CornerLabel.UNKNOWN
        assert finding.severity is None
        assert finding.bounding_box is None


def test_a_white_text_box_in_the_interior_does_not_read_as_whitening() -> None:
    """White print 3 mm inboard is layout, not damage — the band cannot see
    it."""
    picture = a_drawn_card()
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    card[40:80, 40:80] = WHITE_BGR

    corners = classified(picture)

    assert corners[CornerRegion.TOP_LEFT].label is CornerLabel.CLEAN


def test_background_beyond_the_rounded_corner_is_not_whitening() -> None:
    """A real card's corner is a cut arc; the notch outside it is photographed
    background. On a white surface that notch is near-white, and it must not
    read as damage."""
    picture = a_drawn_card()
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    radius = DEFAULT_CORNER_THRESHOLDS.corner_radius_px
    rows, columns = np.mgrid[0:radius, 0:radius]
    notch = (rows - radius) ** 2 + (columns - radius) ** 2 > radius**2
    for flip_rows, flip_columns in ((False, False), (False, True), (True, False), (True, True)):
        tip_rows = slice(CARD_PX_HEIGHT - radius, CARD_PX_HEIGHT) if flip_rows else slice(0, radius)
        tip_columns = (
            slice(CARD_PX_WIDTH - radius, CARD_PX_WIDTH) if flip_columns else slice(0, radius)
        )
        mask = notch
        if flip_rows:
            mask = np.flipud(mask)
        if flip_columns:
            mask = np.fliplr(mask)
        card[tip_rows, tip_columns][mask] = WHITE_BGR

    corners = classified(picture)

    for finding in corners.values():
        assert finding.label is CornerLabel.CLEAN


def test_undecodable_bytes_refuse_the_whole_side() -> None:
    result = classify(b"not a png", card_frame=CARD_FRAME)

    assert isinstance(result, InsufficientInformation)
    assert result.reason is not None
    assert "decoded" in result.reason


def test_a_card_frame_too_small_refuses_the_whole_side() -> None:
    small = BoundingBox(x=0.4, y=0.4, width=0.1, height=0.1)

    result = classify(encoded(a_drawn_card()), card_frame=small)

    assert isinstance(result, InsufficientInformation)
    assert result.reason is not None
    assert "too small" in result.reason


def test_a_pre_194_artifact_with_no_margin_still_classifies() -> None:
    """A pre-#194 artifact has no margin: the caller says so with the whole
    unit square, and the corner crops sit at the image's own corners."""
    picture = a_drawn_card()
    bare = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]

    result = classify(
        encoded(np.ascontiguousarray(bare)),
        card_frame=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    )

    assert not isinstance(result, InsufficientInformation)
    for finding in result.values():
        assert finding.label is CornerLabel.CLEAN


@pytest.mark.parametrize("region", list(CornerRegion))
def test_the_bounding_box_names_where_the_whitening_sits(region: CornerRegion) -> None:
    """The finding's spatial claim is fractions of the whole artifact (§17).

    Asserted as the exact drawn rectangle at every corner, because the
    canonical flips are undone on the way out — a dropped or inverted
    un-flip would mirror the box inside the crop and still pass a
    within-the-crop check.
    """
    picture = a_drawn_card()
    left = region in (CornerRegion.TOP_LEFT, CornerRegion.BOTTOM_LEFT)
    bottom = region in (CornerRegion.BOTTOM_LEFT, CornerRegion.BOTTOM_RIGHT)
    columns = (
        slice(CLEAR_OF_TIP, CLEAR_OF_TIP + 30)
        if left
        else slice(CARD_PX_WIDTH - CLEAR_OF_TIP - 30, CARD_PX_WIDTH - CLEAR_OF_TIP)
    )
    whitened_along_the_top_edge(picture, columns=columns, at_the_bottom=bottom)

    box = classified(picture)[region].bounding_box

    assert isinstance(box, BoundingBox)
    expected_x = MARGIN_PX + columns.start
    expected_y = MARGIN_PX + (CARD_PX_HEIGHT - BAND_BOTTOM if bottom else BAND_TOP)
    assert box.x == pytest.approx(expected_x / WIDTH, abs=1e-9)
    assert box.y == pytest.approx(expected_y / HEIGHT, abs=1e-9)
    assert box.width == pytest.approx(30 / WIDTH, abs=1e-9)
    assert box.height == pytest.approx((BAND_BOTTOM - BAND_TOP) / HEIGHT, abs=1e-9)


def test_thresholds_reject_a_disordered_band() -> None:
    with pytest.raises(ValueError):
        CornerThresholds(moderate_min_area_mm2=5.0)
    with pytest.raises(ValueError):
        CornerThresholds(edge_inset_px=20)
    with pytest.raises(ValueError):
        CornerThresholds(white_border_fraction=1.5)


def test_thresholds_serialise_with_the_corners_prefix() -> None:
    record = DEFAULT_CORNER_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("corners_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_classification() -> None:
    """The pin: changing any threshold's value means bumping this constant."""
    assert CORNERS_VERSION == "corners-opencv-v0.1.0"
