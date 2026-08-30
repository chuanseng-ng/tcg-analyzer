"""Template-aware centering — spec §21, issue #182.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The artifacts are **built in the test**, on the rule
`ml/card-detection/tests/test_card_detection.py` states: a binary blob in the
repository is one nobody can read at review time, and a test asserting that an
off-centre frame measures 0.6 is only convincing if the reader can watch the
borders being drawn 36 and 24 pixels wide.

The card frame is constructed by hand rather than read from a normalization
record. This package does not depend on `ml/normalization` — the frame crosses
between them as a domain type — and a test that reached for the normalizer
would couple what the packaging deliberately keeps apart.

Built with OpenCV rather than Pillow, so this package's tests need nothing this
package does not already depend on.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.condition import BoundingBox, Centering
from tcg_domain.confidence import Confidence, InsufficientInformation
from tcg_ml_centering import (
    CENTERING_VERSION,
    DEFAULT_CENTERING_THRESHOLDS,
    CenteringThresholds,
    SideCentering,
    centering_of,
    measure,
)

#: The real artifact's numbers (#194): 63 x 88 mm at 12 px/mm, inside a 2 mm
#: margin of real photograph. The measurement must never mistake the margin
#: for the card's border.
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

#: The tones: the photographed surface in the margin, the card's printed
#: border, the content region's paper, and its ink. The border is the
#: brightest thing on the card so the frame boundary is a real edge.
SURFACE, BORDER, PAPER, INK = 90, 210, 140, 40

#: Canny places a wall within a pixel or two; on a ~60 px denominator that is
#: what this tolerance absorbs.
RATIO_TOLERANCE = 0.02


def a_drawn_artifact(*, left: int, right: int, top: int, bottom: int) -> NDArray[np.uint8]:
    """An artifact whose four border widths are the arguments, in pixels.

    Grey photographed surface in the margin, a bright printed border, then a
    checkered content region — so the border/content boundary is the largest
    frame-like contour on the card, and the expected ratios are hand
    computable: ``left=36, right=24`` is ``36 / 60 = 0.6``.
    """
    rng = np.random.default_rng(7)
    surface = rng.integers(SURFACE - 10, SURFACE + 10, (HEIGHT, WIDTH), np.uint8)
    picture = np.stack([surface] * 3, axis=2)

    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    card[:, :] = BORDER

    content = card[top : CARD_PX_HEIGHT - bottom, left : CARD_PX_WIDTH - right]
    content[:, :] = PAPER
    rows, columns = np.mgrid[0 : content.shape[0], 0 : content.shape[1]]
    content[(((rows // 40) + (columns // 40)) % 2).astype(bool)] = INK
    return picture


def encoded(picture: NDArray[np.uint8]) -> bytes:
    ok, data = cv2.imencode(".png", picture)
    assert ok
    return bytes(data.tobytes())


def an_artifact(*, left: int, right: int, top: int, bottom: int) -> bytes:
    return encoded(a_drawn_artifact(left=left, right=right, top=top, bottom=bottom))


def test_an_evenly_bordered_card_measures_half_on_both_axes() -> None:
    side = measure(an_artifact(left=36, right=36, top=36, bottom=36), card_frame=CARD_FRAME)

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, float)
    assert isinstance(side.vertical, float)
    assert abs(side.horizontal - 0.5) <= RATIO_TOLERANCE
    assert abs(side.vertical - 0.5) <= RATIO_TOLERANCE
    assert 0.0 < side.confidence.value <= 1.0


def test_an_off_centre_card_measures_its_ratios() -> None:
    side = measure(an_artifact(left=36, right=24, top=48, bottom=24), card_frame=CARD_FRAME)

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, float)
    assert isinstance(side.vertical, float)
    assert abs(side.horizontal - 36 / 60) <= RATIO_TOLERANCE
    assert abs(side.vertical - 48 / 72) <= RATIO_TOLERANCE


def test_rotating_card_and_frame_together_mirrors_the_ratios() -> None:
    """A 180-degree turn swaps left with right and top with bottom.

    The rotation-invariance requirement in its hand-calculable form: the
    measurement must be a property of card and frame together, not of where
    the frame sits in the picture.
    """
    turned = np.rot90(a_drawn_artifact(left=36, right=24, top=48, bottom=24), 2)

    side = measure(encoded(np.ascontiguousarray(turned)), card_frame=CARD_FRAME)

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, float)
    assert isinstance(side.vertical, float)
    assert abs(side.horizontal - 24 / 60) <= RATIO_TOLERANCE
    assert abs(side.vertical - 24 / 72) <= RATIO_TOLERANCE


def test_a_slightly_skewed_frame_still_measures_by_midpoints() -> None:
    """Print registration can rotate the frame a hair against the card.

    The borders must be the midpoint-to-side distances of the annotation
    tool's quad rule — a naive bounding box around a rotated frame widens
    with the rotation and would misread every border.
    """
    angle = math.radians(1.5)
    corners = [(36.0, 48.0), (732.0, 48.0), (732.0, 1032.0), (36.0, 1032.0)]
    centre_x = sum(x for x, _y in corners) / 4.0
    centre_y = sum(y for _x, y in corners) / 4.0

    def rotated(x: float, y: float) -> tuple[float, float]:
        return (
            centre_x + (x - centre_x) * math.cos(angle) - (y - centre_y) * math.sin(angle),
            centre_y + (x - centre_x) * math.sin(angle) + (y - centre_y) * math.cos(angle),
        )

    rng = np.random.default_rng(7)
    surface = rng.integers(SURFACE - 10, SURFACE + 10, (HEIGHT, WIDTH), np.uint8)
    picture = np.stack([surface] * 3, axis=2)
    card = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]
    card[:, :] = BORDER
    window = np.array([rotated(x, y) for x, y in corners], np.int32)
    cv2.fillPoly(card, [window], (INK, INK, INK))

    side = measure(encoded(picture), card_frame=CARD_FRAME)

    left_mid = rotated(36.0, (48.0 + 1032.0) / 2.0)
    right_mid = rotated(732.0, (48.0 + 1032.0) / 2.0)
    top_mid = rotated((36.0 + 732.0) / 2.0, 48.0)
    bottom_mid = rotated((36.0 + 732.0) / 2.0, 1032.0)
    left, right = left_mid[0], CARD_PX_WIDTH - right_mid[0]
    top, bottom = top_mid[1], CARD_PX_HEIGHT - bottom_mid[1]

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, float)
    assert isinstance(side.vertical, float)
    assert abs(side.horizontal - left / (left + right)) <= RATIO_TOLERANCE
    assert abs(side.vertical - top / (top + bottom)) <= RATIO_TOLERANCE


def test_a_full_art_card_is_refused_not_measured() -> None:
    """Spec §21's core: a template with no frame is not measured against one."""
    rng = np.random.default_rng(11)
    picture = rng.integers(30, 226, (HEIGHT, WIDTH, 3), np.uint8)

    side = measure(encoded(np.ascontiguousarray(picture)), card_frame=CARD_FRAME)

    assert isinstance(side, InsufficientInformation)
    assert side.reason is not None
    assert "full-art" in side.reason


def test_a_borderless_axis_is_refused_per_axis() -> None:
    """A frame touching the card edge leaves no border to ratio on that axis.

    #160's zero-denominator rule: the refused axis is
    `insufficient_information`, never `0.0` and never a whole-side failure —
    the other axis's border is real and is measured.
    """
    side = measure(an_artifact(left=0, right=0, top=36, bottom=36), card_frame=CARD_FRAME)

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, InsufficientInformation)
    assert isinstance(side.vertical, float)
    assert abs(side.vertical - 0.5) <= RATIO_TOLERANCE


def test_an_implausibly_thick_border_refuses_the_side() -> None:
    """No real layout has a 12 mm border; a quad with one is an artwork window.

    The whole side refuses — one absurd border means the quadrilateral is not
    the frame, so the other axis is not trustworthy either (#176's lesson).
    """
    side = measure(an_artifact(left=150, right=30, top=30, bottom=30), card_frame=CARD_FRAME)

    assert isinstance(side, InsufficientInformation)
    assert side.reason is not None
    assert "implausibly thick" in side.reason


def test_undecodable_bytes_are_refused_not_raised() -> None:
    side = measure(b"not a png", card_frame=CARD_FRAME)

    assert isinstance(side, InsufficientInformation)
    assert side.reason is not None
    assert "decoded" in side.reason


def test_a_pre_194_whole_square_frame_still_measures() -> None:
    """A pre-#194 artifact has no margin: its card really reaches the edges.

    The caller says so with the whole unit square, and the measurement must
    not assume today's margin.
    """
    picture = a_drawn_artifact(left=36, right=24, top=48, bottom=24)
    bare = picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]

    side = measure(
        encoded(np.ascontiguousarray(bare)),
        card_frame=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    )

    assert isinstance(side, SideCentering)
    assert isinstance(side.horizontal, float)
    assert isinstance(side.vertical, float)
    assert abs(side.horizontal - 36 / 60) <= RATIO_TOLERANCE
    assert abs(side.vertical - 48 / 72) <= RATIO_TOLERANCE


def test_a_side_with_nothing_measured_is_not_constructible() -> None:
    """Both axes refused is the whole-side refusal, spelled as the result.

    A confidence over zero measurements is a confidence about nothing —
    `tcg_domain.condition.Centering`'s own rule, mirrored per side.
    """
    with pytest.raises(ValueError):
        SideCentering(
            horizontal=InsufficientInformation(),
            vertical=InsufficientInformation(),
            confidence=Confidence.of(0.5),
        )


def test_two_measured_sides_compose_into_a_centering() -> None:
    """The block's confidence is the weaker side's — min, never a product."""
    front = SideCentering(horizontal=0.6, vertical=0.5, confidence=Confidence.of(0.9))
    back = SideCentering(horizontal=0.45, vertical=0.55, confidence=Confidence.of(0.7))

    block = centering_of(front, back)

    assert isinstance(block, Centering)
    assert block.front_horizontal == 0.6
    assert block.front_vertical == 0.5
    assert block.back_horizontal == 0.45
    assert block.back_vertical == 0.55
    assert block.confidence == Confidence.of(0.7)


def test_a_refused_side_leaves_the_other_sides_ratios() -> None:
    """One side's refusal rides per ratio, wearing its own reason."""
    front = SideCentering(horizontal=0.6, vertical=0.5, confidence=Confidence.of(0.9))
    back = InsufficientInformation("no printed border frame was found")

    block = centering_of(front, back)

    assert isinstance(block, Centering)
    assert block.front_horizontal == 0.6
    assert block.front_vertical == 0.5
    assert isinstance(block.back_horizontal, InsufficientInformation)
    assert isinstance(block.back_vertical, InsufficientInformation)
    assert block.back_horizontal.reason == back.reason
    assert block.confidence == Confidence.of(0.9)


def test_two_refused_sides_are_the_axis_refusal() -> None:
    """A `Centering` with nothing measured is unconstructible — the whole-axis
    refusal is `InsufficientInformation` itself."""
    block = centering_of(
        InsufficientInformation("no printed border frame was found"),
        InsufficientInformation("the artifact could not be decoded"),
    )

    assert isinstance(block, InsufficientInformation)


def test_thresholds_reject_a_disordered_band() -> None:
    with pytest.raises(ValueError):
        CenteringThresholds(min_frame_area_fraction=0.9, max_frame_area_fraction=0.5)
    with pytest.raises(ValueError):
        CenteringThresholds(approx_epsilon=1.5)


def test_thresholds_serialise_with_the_centering_prefix() -> None:
    record = DEFAULT_CENTERING_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("centering_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_measurement() -> None:
    """The pin: changing any threshold's value means bumping this constant."""
    assert CENTERING_VERSION == "centering-opencv-v0.1.0"
