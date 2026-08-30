"""Perspective correction and normalization — spec §18, issue #38.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The photographs are **built in the test**, on the rule
`ml/card-detection/tests/test_card_detection.py` states: a binary blob in the
repository is one nobody can read at review time, and a test asserting that a
skewed card comes back rectangular is only convincing if the reader can watch it
being skewed.

The quadrilateral is constructed by hand rather than taken from `detect`. This
package does not depend on `ml/card-detection` — the geometry crosses between
them as a domain type — and a test that reached for the detector would couple
what the packaging deliberately keeps apart, as well as making a failure here
indistinguishable from a detection failure.

Built with OpenCV rather than Pillow, so this package's tests need nothing this
package does not already depend on.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.card_geometry import CardGeometry, Corner
from tcg_domain.confidence import Confidence, InsufficientInformation
from tcg_ml_normalization import (
    DEFAULT_NORMALIZATION_THRESHOLDS,
    NORMALIZATION_VERSION,
    NormalizationThresholds,
    Normalized,
    normalize,
)

#: The frame the card is photographed in, portrait and large enough that the
#: warp's intermediate is a real downscale rather than a no-op.
HEIGHT, WIDTH = 2400, 1800

#: The card, at the real thing's 63:88 proportions and comfortably inside the
#: frame. Every positional assertion below is against these four numbers.
CARD_LEFT, CARD_TOP = 400, 500
CARD_WIDTH, CARD_HEIGHT = 945, 1320

#: The tones the card face is painted in. Deliberately a narrow band well inside
#: [0, 255], so that a contrast stretch anywhere in the pipeline shows up as a
#: band that came back wider than it went in.
INK, PAPER = 100, 140

#: The card's own pixels in the artifact, and the background margin around it
#: (#194): 63 x 88 mm at 12 px/mm, inside a 2 mm frame of real photograph.
CARD_PX_WIDTH, CARD_PX_HEIGHT = 756, 1056
MARGIN_PX = 24
TARGET_WIDTH, TARGET_HEIGHT = CARD_PX_WIDTH + 2 * MARGIN_PX, CARD_PX_HEIGHT + 2 * MARGIN_PX


def border_width(width: int, height: int) -> int:
    """The dark frame a card face is printed with, in its own pixels.

    Measured from the *short* edge, so that a landscape face and a portrait one
    of the same card carry the same border — which is what lets the artifact's
    four measured borders be compared against one number whichever way up the
    card was photographed.
    """
    return max(2, min(width, height) // 30)


def a_face(width: int, height: int) -> NDArray[np.uint8]:
    """A card face: a dark border, a plain gutter inside it, then printing.

    The border is what makes "did this come back rectangular" answerable — a
    squashed or sheared card has borders of unequal width. The gutter is what
    makes that measurable: printing that abutted the border would extend the
    run being measured by however much of a check happened to be dark there,
    and the number would be about the fixture rather than about the warp.
    """
    face = np.full((height, width, 3), PAPER, np.uint8)

    frame = border_width(width, height)
    printing = slice(4 * frame, -4 * frame)
    rows, columns = np.mgrid[0:height, 0:width][:, printing, printing]
    face[printing, printing][(((rows // 40) + (columns // 40)) % 2).astype(bool)] = INK

    face[:frame, :] = INK
    face[-frame:, :] = INK
    face[:, :frame] = INK
    face[:, -frame:] = INK
    return face


def borders(picture: NDArray[np.uint8]) -> tuple[int, int, int, int]:
    """The four border widths in the artifact — left, right, top, bottom.

    Read across the middle of the card, where the gutter guarantees the run
    ends at the border's inner edge. The margin is sliced off first: it holds
    the photograph's own dark surface, which is not the card's border.
    """
    card = picture[MARGIN_PX:-MARGIN_PX, MARGIN_PX:-MARGIN_PX]
    dark = (card.mean(axis=2) < (INK + PAPER) / 2).astype(np.uint8)
    row, column = dark.shape[0] // 2, dark.shape[1] // 2
    return (
        _run(dark[row, :]),
        _run(dark[row, ::-1]),
        _run(dark[:, column]),
        _run(dark[::-1, column]),
    )


def photograph(
    *,
    corners: tuple[Corner, Corner, Corner, Corner] | None = None,
    landscape: bool = False,
) -> tuple[bytes, tuple[Corner, Corner, Corner, Corner]]:
    """A card placed in a frame, and where its corners ended up.

    With no `corners` the card is laid down square; with them it is projected
    onto that quadrilateral, which is how a skewed photograph is built.
    """
    width, height = (CARD_HEIGHT, CARD_WIDTH) if landscape else (CARD_WIDTH, CARD_HEIGHT)
    if corners is None:
        corners = (
            (float(CARD_LEFT), float(CARD_TOP)),
            (float(CARD_LEFT + width), float(CARD_TOP)),
            (float(CARD_LEFT + width), float(CARD_TOP + height)),
            (float(CARD_LEFT), float(CARD_TOP + height)),
        )

    # A flat fill is not a photograph, and a surface with no grain is a case
    # that never arrives.
    rng = np.random.default_rng(0)
    surface = np.clip(
        np.full((HEIGHT, WIDTH, 3), 40, np.int16) + rng.integers(-8, 9, size=(HEIGHT, WIDTH, 1)),
        0,
        255,
    ).astype(np.uint8)

    face = a_face(width, height)
    onto = cv2.getPerspectiveTransform(
        np.array(
            [(0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0)],
            dtype=np.float32,
        ),
        np.array(corners, dtype=np.float32),
    )
    placed = cv2.warpPerspective(
        face, onto, (WIDTH, HEIGHT), dst=surface.copy(), borderMode=cv2.BORDER_TRANSPARENT
    )
    return bytes(cv2.imencode(".png", placed)[1].tobytes()), corners


def a_geometry(corners: tuple[Corner, Corner, Corner, Corner]) -> CardGeometry:
    return CardGeometry(
        corners=corners,
        confidence=Confidence.of(0.9),
        frame_width=WIDTH,
        frame_height=HEIGHT,
        detector="a-detector-v0.0.0",
    )


def normalized(**kwargs: object) -> Normalized:
    """`normalize` on a freshly built photograph, refusing an uncertain answer."""
    data, corners = photograph(**kwargs)  # type: ignore[arg-type]
    result = normalize(data, a_geometry(corners))
    assert isinstance(result, Normalized), result
    return result


def decoded(artifact: Normalized) -> NDArray[np.uint8]:
    picture = cv2.imdecode(np.frombuffer(artifact.data, np.uint8), cv2.IMREAD_COLOR)
    assert picture is not None
    return picture  # type: ignore[no-any-return]


# --------------------------------------------------------------------------
# The artifact
# --------------------------------------------------------------------------


def test_a_square_card_normalizes_to_the_target_resolution() -> None:
    artifact = normalized()

    assert (artifact.width, artifact.height) == (TARGET_WIDTH, TARGET_HEIGHT)
    assert decoded(artifact).shape == (TARGET_HEIGHT, TARGET_WIDTH, 3)


def test_the_card_within_the_artifact_is_exactly_the_proportions_of_a_real_card() -> None:
    """63 x 88 mm at 12 px/mm, so a centering ratio measured on it means what it says.

    The margin sits *around* that: the artifact is larger, but the card's own
    rectangle keeps a real card's proportions with no rounding.
    """
    assert pytest.approx(63.0 / 88.0, abs=1e-12) == CARD_PX_WIDTH / CARD_PX_HEIGHT


def test_a_skewed_photograph_comes_back_rectangular() -> None:
    """The point of the stage: the card's own border is even on all four sides.

    A card photographed at an angle has a border that is wider on the near edge
    than the far one. If perspective correction worked, the four borders in the
    artifact are the same width; if it silently did nothing, they are not.
    """
    skewed = (
        (520.0, 470.0),
        (1430.0, 640.0),
        (1330.0, 1900.0),
        (430.0, 1720.0),
    )
    artifact = normalized(corners=skewed)

    expected = border_width(CARD_WIDTH, CARD_HEIGHT) * (CARD_PX_HEIGHT / CARD_HEIGHT)
    measured = borders(decoded(artifact))
    for side in measured:
        assert side == pytest.approx(expected, abs=2), measured


def test_a_card_photographed_on_its_side_is_still_the_right_way_round() -> None:
    """The detector anchors its traversal at the corner nearest the frame origin.

    So for a landscape card `corners[0] -> corners[1]` is the *long* edge, and
    warping that straight onto a portrait target squashes the card into the
    wrong proportions. The corner tuple is rotated instead.

    The border is what proves it. Squashed, the artifact's horizontal borders
    would come back at 0.57x the face's and its vertical ones at 1.12x; correct,
    all four land on the same 0.8x, because the long edge and the short one are
    scaled by the same factor.
    """
    artifact = normalized(landscape=True)

    assert (artifact.width, artifact.height) == (TARGET_WIDTH, TARGET_HEIGHT)
    assert artifact.quarter_turns == 1

    expected = border_width(CARD_HEIGHT, CARD_WIDTH) * (CARD_PX_HEIGHT / CARD_HEIGHT)
    measured = borders(decoded(artifact))
    for side in measured:
        assert side == pytest.approx(expected, abs=2), measured


def test_a_card_photographed_upright_is_not_rotated() -> None:
    assert normalized().quarter_turns == 0


# --------------------------------------------------------------------------
# The transform
# --------------------------------------------------------------------------


def test_the_transform_round_trips_a_known_point() -> None:
    """§51's post-V1 defect visualisation draws boxes on the original.

    Which is only possible if a point in the artifact can be put back where it
    came from, so the matrix is asserted in both directions rather than merely
    stored.
    """
    data, corners = photograph()
    artifact = normalize(data, a_geometry(corners))
    assert isinstance(artifact, Normalized)

    matrix = np.array(artifact.matrix, dtype=np.float64).reshape(3, 3)
    corner_in_original = np.array([[list(corners[0])]], dtype=np.float64)

    forward = cv2.perspectiveTransform(corner_in_original, matrix)
    back = cv2.perspectiveTransform(forward, np.linalg.inv(matrix))

    # The card's first corner is the inner rectangle's first corner — the
    # margin is what sits between it and the artifact's own origin (#194).
    assert forward[0][0][0] == pytest.approx(MARGIN_PX, abs=1.5)
    assert forward[0][0][1] == pytest.approx(MARGIN_PX, abs=1.5)
    assert back[0][0] == pytest.approx(corner_in_original[0][0], abs=1e-6)


def test_the_transform_maps_the_far_corner_to_the_far_corner() -> None:
    data, corners = photograph()
    artifact = normalize(data, a_geometry(corners))
    assert isinstance(artifact, Normalized)

    matrix = np.array(artifact.matrix, dtype=np.float64).reshape(3, 3)
    mapped = cv2.perspectiveTransform(np.array([[list(corners[2])]], dtype=np.float64), matrix)

    assert mapped[0][0][0] == pytest.approx(MARGIN_PX + CARD_PX_WIDTH, abs=1.5)
    assert mapped[0][0][1] == pytest.approx(MARGIN_PX + CARD_PX_HEIGHT, abs=1.5)


def test_the_record_carries_everything_needed_to_explain_the_artifact() -> None:
    record = normalized().as_record()

    assert record["version"] == NORMALIZATION_VERSION
    assert record["width"] == TARGET_WIDTH
    assert record["height"] == TARGET_HEIGHT
    assert isinstance(record["matrix"], list)
    assert len(record["matrix"]) == 9
    assert record["thresholds"] == DEFAULT_NORMALIZATION_THRESHOLDS.as_record()
    # The bytes are the object, not the record.
    assert "data" not in record


def test_the_thresholds_record_is_prefixed_so_it_cannot_collide() -> None:
    assert all(
        key.startswith("normalization_") for key in DEFAULT_NORMALIZATION_THRESHOLDS.as_record()
    )


# --------------------------------------------------------------------------
# What the stage must never do
# --------------------------------------------------------------------------


def test_the_photograph_is_not_enhanced() -> None:
    """The card is painted in a 100-140 band, and must come back inside it.

    A contrast stretch, a histogram equalisation or an unsharp mask would all
    widen that band — and each of them fabricates or erases exactly the
    whitening and scratches every stage downstream exists to measure.
    """
    picture = decoded(normalized())

    # Away from the card's edges and clear of the margin, where the warp
    # samples the surface underneath.
    inside = picture[MARGIN_PX + 80 : -(MARGIN_PX + 80), MARGIN_PX + 80 : -(MARGIN_PX + 80)]
    assert inside.min() >= INK - 6, inside.min()
    assert inside.max() <= PAPER + 6, inside.max()


def test_the_same_photograph_twice_gives_the_same_bytes() -> None:
    data, corners = photograph()
    geometry = a_geometry(corners)

    assert normalize(data, geometry) == normalize(data, geometry)


def test_the_artifact_is_lossless() -> None:
    """PNG, not JPEG: 8x8 blocking is fabricated surface texture."""
    artifact = normalized()

    assert artifact.media_type == "image/png"
    assert artifact.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_bytes_that_do_not_decode_are_answered_rather_than_raised() -> None:
    """`tcg_ml_image_quality.UnreadableImage` stays the one owner of that failure."""
    _, corners = photograph()

    result = normalize(b"not an image", a_geometry(corners))

    assert isinstance(result, InsufficientInformation)
    assert not result
    assert result.reason is not None


# --------------------------------------------------------------------------
# The thresholds
# --------------------------------------------------------------------------


def test_a_resolution_that_lands_mid_pixel_is_refused() -> None:
    """The 63:88 claim is why 12 px/mm was chosen, so it is checked not trusted."""
    with pytest.raises(ValueError, match="whole number of pixels"):
        NormalizationThresholds(pixels_per_mm=12.5)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pixels_per_mm", 0.0),
        ("max_warp_multiple", 0),
        ("png_compression", 10),
        ("margin_mm", -1.0),
    ],
)
def test_a_threshold_outside_its_range_is_refused(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        NormalizationThresholds(**{field: value})  # type: ignore[arg-type]


def test_a_lower_warp_multiple_still_produces_the_same_sized_artifact() -> None:
    """The intermediate is a quality knob, never a shape one."""
    data, corners = photograph()

    coarse = normalize(
        data, a_geometry(corners), thresholds=NormalizationThresholds(max_warp_multiple=1)
    )

    assert isinstance(coarse, Normalized)
    assert (coarse.width, coarse.height) == (TARGET_WIDTH, TARGET_HEIGHT)


def _run(line: NDArray[np.uint8]) -> int:
    """How many pixels the leading run of set values covers."""
    unset = np.flatnonzero(line == 0)
    return int(unset[0]) if unset.size else int(line.size)


# --------------------------------------------------------------------------
# The margin — #194
# --------------------------------------------------------------------------


def test_the_margin_holds_the_photographs_own_background() -> None:
    """The margin exists so an annotator can read the card's edge against what
    it was photographed on — so it must be the real surface, not a painted
    border."""
    picture = decoded(normalized())

    surface_tone = 40
    for band in (
        picture[: MARGIN_PX - 2, :],
        picture[-(MARGIN_PX - 2) :, :],
        picture[:, : MARGIN_PX - 2],
        picture[:, -(MARGIN_PX - 2) :],
    ):
        assert abs(float(band.mean()) - surface_tone) < 12, band.mean()


def test_a_zero_margin_is_the_bare_card() -> None:
    """`margin_mm=0` is the pre-#194 artifact exactly — the option is a size,
    never a mode."""
    data, corners = photograph()

    bare = normalize(data, a_geometry(corners), thresholds=NormalizationThresholds(margin_mm=0.0))

    assert isinstance(bare, Normalized)
    assert (bare.width, bare.height) == (CARD_PX_WIDTH, CARD_PX_HEIGHT)


def test_a_margin_that_lands_mid_pixel_is_refused() -> None:
    """The card's rectangle must sit on whole pixels inside the artifact, or a
    stored fraction of the artifact stops naming an exact place on the card."""
    with pytest.raises(ValueError, match="whole number of pixels"):
        NormalizationThresholds(margin_mm=0.1)


def test_the_record_names_the_margin() -> None:
    """The card's inner rectangle must be derivable from the stored record
    alone — the server that serves `card_frame` may not import this package."""
    record = DEFAULT_NORMALIZATION_THRESHOLDS.as_record()

    assert record["normalization_margin_mm"] == 2.0
    assert record["normalization_pixels_per_mm"] == 12.0
