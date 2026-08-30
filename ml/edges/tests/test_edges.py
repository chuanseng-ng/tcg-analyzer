"""Edge classification — spec §15, issue #184.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The artifacts are **built in the test**, on the rule
`ml/corners/tests/test_corners.py` restates: a binary blob in the repository
is one nobody can read at review time, and a test asserting that an 864-pixel
white stretch is moderate whitening is only convincing if the reader can
watch the stretch being drawn 12 pixels deep and 72 wide.

The card frame is constructed by hand rather than read from a normalization
record — this package does not depend on `ml/normalization`; the frame
crosses between them as a domain type.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.annotation import DefectSeverity, EdgeLabel, EdgeRegion
from tcg_domain.condition import BoundingBox, RegionFinding
from tcg_domain.confidence import InsufficientInformation
from tcg_ml_edges import (
    DEFAULT_EDGE_THRESHOLDS,
    EDGES_VERSION,
    EdgeThresholds,
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

#: Exposed paper core along a worn edge (BGR) — near-white.
WHITE_BGR = (245, 245, 245)

#: The default detection band: depth [2, 14) from the card edge. Stretches
#: drawn inside it, clear of the 84 px corner exclusion at either end, land
#: wholly in the detection region, so their areas are hand-computable:
#: 12 px deep by 72 px long is 864 px = 6.0 mm² at 144 px²/mm².
BAND_TOP, BAND_BOTTOM = 2, 14
CORNER_EXCLUSION = 84

#: Where along the run the test stretches start — inside every edge's run
#: ([84, 672) along the top and bottom, [84, 972) along the left and right).
ALONG = 200


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


def whitened_along_the_edge(
    picture: NDArray[np.uint8], *, region: EdgeRegion, along: slice
) -> None:
    """Paint exposed core inside one edge's detection band, in card pixels."""
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    if region is EdgeRegion.TOP:
        card[BAND_TOP:BAND_BOTTOM, along] = WHITE_BGR
    elif region is EdgeRegion.BOTTOM:
        card[CARD_PX_HEIGHT - BAND_BOTTOM : CARD_PX_HEIGHT - BAND_TOP, along] = WHITE_BGR
    elif region is EdgeRegion.LEFT:
        card[along, BAND_TOP:BAND_BOTTOM] = WHITE_BGR
    else:
        card[along, CARD_PX_WIDTH - BAND_BOTTOM : CARD_PX_WIDTH - BAND_TOP] = WHITE_BGR


def encoded(picture: NDArray[np.uint8]) -> bytes:
    ok, data = cv2.imencode(".png", picture)
    assert ok
    return bytes(data.tobytes())


def classified(picture: NDArray[np.uint8]) -> dict[EdgeRegion, RegionFinding]:
    result = classify(encoded(picture), card_frame=CARD_FRAME)
    assert not isinstance(result, InsufficientInformation)
    return dict(result)


def test_a_clean_card_reports_all_four_edges_clean() -> None:
    edges = classified(a_drawn_card())

    assert set(edges) == set(EdgeRegion)
    for finding in edges.values():
        assert finding.label is EdgeLabel.CLEAN
        assert finding.severity is None
        assert finding.bounding_box is None
        assert finding.confidence.value >= 0.9


def test_a_short_white_stretch_is_minor_whitening() -> None:
    """144 px inside the band is 1.0 mm² — past the 0.5 mm² floor, nowhere
    near moderate."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=EdgeRegion.TOP, along=slice(ALONG, ALONG + 12))

    edges = classified(picture)

    assert edges[EdgeRegion.TOP].label is EdgeLabel.WHITENING
    assert edges[EdgeRegion.TOP].severity is DefectSeverity.MINOR
    assert edges[EdgeRegion.RIGHT].label is EdgeLabel.CLEAN
    assert edges[EdgeRegion.BOTTOM].label is EdgeLabel.CLEAN
    assert edges[EdgeRegion.LEFT].label is EdgeLabel.CLEAN


def test_a_visible_stretch_is_moderate_whitening() -> None:
    """864 px is 6.0 mm² — between the 4.0 and 10.0 mm² boundaries."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=EdgeRegion.TOP, along=slice(ALONG, ALONG + 72))

    edges = classified(picture)

    assert edges[EdgeRegion.TOP].label is EdgeLabel.WHITENING
    assert edges[EdgeRegion.TOP].severity is DefectSeverity.MODERATE


def test_a_long_whitened_run_is_severe() -> None:
    """A 144-column run of the 12-px band: 1728 px = 12.0 mm²."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=EdgeRegion.TOP, along=slice(ALONG, ALONG + 144))

    edges = classified(picture)

    assert edges[EdgeRegion.TOP].label is EdgeLabel.WHITENING
    assert edges[EdgeRegion.TOP].severity is DefectSeverity.SEVERE


@pytest.mark.parametrize("region", list(EdgeRegion))
def test_whitening_is_reported_at_the_edge_that_has_it(region: EdgeRegion) -> None:
    """One code path serves four edges via each region's own depth axis; every
    orientation is exercised."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=region, along=slice(ALONG, ALONG + 72))

    edges = classified(picture)

    for candidate, finding in edges.items():
        if candidate is region:
            assert finding.label is EdgeLabel.WHITENING
        else:
            assert finding.label is EdgeLabel.CLEAN


def test_whitening_inside_the_corner_zone_belongs_to_no_edge() -> None:
    """The corner/edge boundary, made checkable: the first 84 px of every
    edge's run are the corner analyzer's 7 mm crop (#183), and a defect
    there is the corner result's — reporting it here would double-report
    one defect across two axes (#184's non-goal)."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=EdgeRegion.TOP, along=slice(0, CORNER_EXCLUSION))

    edges = classified(picture)

    for finding in edges.values():
        assert finding.label is EdgeLabel.CLEAN


def test_a_white_bordered_card_answers_unknown_for_every_edge() -> None:
    """A near-white printed border makes whitening indistinguishable in this
    signal — guessing clean there is the confidently-wrong output §2.7
    forbids, so each edge refuses as `unknown`."""
    edges = classified(a_drawn_card(border_bgr=(240, 240, 240)))

    for finding in edges.values():
        assert finding.label is EdgeLabel.UNKNOWN
        assert finding.severity is None
        assert finding.bounding_box is None
        assert finding.confidence.value == 0.5


def test_whitening_straddling_the_seam_reports_only_the_edge_half() -> None:
    """The other half of the boundary contract: a patch crossing the 84 px
    seam is reported by the edge for its own half only, with the box
    starting at the seam — the corner half is the corner analyzer's."""
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=EdgeRegion.TOP, along=slice(70, 100))

    finding = classified(picture)[EdgeRegion.TOP]

    assert finding.label is EdgeLabel.WHITENING
    box = finding.bounding_box
    assert isinstance(box, BoundingBox)
    assert box.x == pytest.approx((MARGIN_PX + CORNER_EXCLUSION) / WIDTH, abs=1e-9)
    assert box.width == pytest.approx((100 - CORNER_EXCLUSION) / WIDTH, abs=1e-9)


def test_a_white_text_box_in_the_interior_does_not_read_as_whitening() -> None:
    """White print 3 mm inboard is layout, not damage — the band cannot see
    it."""
    picture = a_drawn_card()
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    card[40:80, 200:240] = WHITE_BGR

    edges = classified(picture)

    for finding in edges.values():
        assert finding.label is EdgeLabel.CLEAN


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
    unit square, and the edge bands sit at the image's own borders."""
    picture = a_drawn_card()
    bare = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]

    result = classify(
        encoded(np.ascontiguousarray(bare)),
        card_frame=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    )

    assert not isinstance(result, InsufficientInformation)
    for finding in result.values():
        assert finding.label is EdgeLabel.CLEAN


@pytest.mark.parametrize("region", list(EdgeRegion))
def test_the_bounding_box_names_where_the_whitening_sits(region: EdgeRegion) -> None:
    """The finding's spatial claim is fractions of the whole artifact (§17).

    Asserted as the exact drawn rectangle at every edge, because each
    region's band is built in card coordinates — a depth axis mixed up
    between two edges would misplace the box and still pass a
    label-only check.
    """
    picture = a_drawn_card()
    whitened_along_the_edge(picture, region=region, along=slice(ALONG, ALONG + 72))

    box = classified(picture)[region].bounding_box

    assert isinstance(box, BoundingBox)
    if region in (EdgeRegion.TOP, EdgeRegion.BOTTOM):
        expected_x = MARGIN_PX + ALONG
        expected_y = MARGIN_PX + (
            CARD_PX_HEIGHT - BAND_BOTTOM if region is EdgeRegion.BOTTOM else BAND_TOP
        )
        expected_width, expected_height = 72, BAND_BOTTOM - BAND_TOP
    else:
        expected_x = MARGIN_PX + (
            CARD_PX_WIDTH - BAND_BOTTOM if region is EdgeRegion.RIGHT else BAND_TOP
        )
        expected_y = MARGIN_PX + ALONG
        expected_width, expected_height = BAND_BOTTOM - BAND_TOP, 72
    assert box.x == pytest.approx(expected_x / WIDTH, abs=1e-9)
    assert box.y == pytest.approx(expected_y / HEIGHT, abs=1e-9)
    assert box.width == pytest.approx(expected_width / WIDTH, abs=1e-9)
    assert box.height == pytest.approx(expected_height / HEIGHT, abs=1e-9)


def test_thresholds_reject_a_disordered_band() -> None:
    with pytest.raises(ValueError):
        EdgeThresholds(moderate_min_area_mm2=12.0)
    with pytest.raises(ValueError):
        EdgeThresholds(edge_inset_px=20)
    with pytest.raises(ValueError):
        EdgeThresholds(white_border_fraction=1.5)
    with pytest.raises(ValueError):
        EdgeThresholds(corner_exclusion_px=0)


def test_thresholds_serialise_with_the_edges_prefix() -> None:
    record = DEFAULT_EDGE_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("edges_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_classification() -> None:
    """The pin: changing any threshold's value means bumping this constant."""
    assert EDGES_VERSION == "edges-opencv-v0.1.0"
