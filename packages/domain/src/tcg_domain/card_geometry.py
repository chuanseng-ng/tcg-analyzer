"""Where the card is in the photograph — spec §18, issue #37.

Spec §18 puts card detection between the image-quality gate and perspective
correction, and #36 left the gate five conditions it could not answer without
this: perspective distortion, card partly outside frame, multiple cards, sleeve
obstruction and insufficient card size. :class:`CardGeometry` is what the
detector concludes and what those five are judged from.

**Why this is in the domain rather than beside the detector.** Two ml packages
need it — `ml/card-detection` produces it and `ml/image-quality` consumes it as
:func:`~tcg_ml_image_quality.assess`'s `geometry` argument — and a package that
consumes it must not have to depend on the package that produces it. That would
couple two siblings and put a detector in the gate's dependency tree for the
sake of a dataclass. Both already depend on this package, which is stdlib-only
and enforced so by `test_domain_purity.py`. It is the same argument
`image_quality.py` makes for §19's vocabulary, and it lands the same way.

**The corner order is validated, not documented.** Perspective correction reads
the four corners positionally, so an order that is merely conventional does not
fail when it is wrong — it silently rotates or mirrors the card, and every
downstream measurement of a corner or an edge is then about the wrong corner or
edge. :meth:`CardGeometry.__post_init__` therefore refuses a quadrilateral whose
signed area is not positive, which in image coordinates (y increasing downward)
is exactly "the corners run clockwise". A counter-clockwise or mirrored
quadrilateral is not representable.

**Detection failure is a result, not an exception.** A detector that cannot find
a card returns :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION` rather
than raising or than guessing a quadrilateral — spec §2.7, and #37's acceptance
criterion in as many words: failures degrade into the quality gate instead of
producing a wrong quadrilateral.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from tcg_domain.confidence import Confidence
from tcg_domain.errors import InvalidCardGeometry

__all__ = ["CORNER_NAMES", "CardGeometry", "Corner"]

#: One corner, ``(x, y)``, in the coordinates of the **original** photograph —
#: not of whatever working copy the detector measured. Everything downstream
#: crops and warps the original, and a coordinate space that depends on a
#: detector's internal downscale is a coordinate space that changes when the
#: detector is tuned.
type Corner = tuple[float, float]

#: The four corners in the order :attr:`CardGeometry.corners` holds them.
#: Clockwise from the top left, which is the order perspective correction and
#: every later edge and corner measurement read positionally.
CORNER_NAMES: Final = ("top_left", "top_right", "bottom_right", "bottom_left")

_NO_THRESHOLDS: Final[Mapping[str, float]] = MappingProxyType({})


def _validated_corner(value: object) -> Corner:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise InvalidCardGeometry(f"a corner must be an (x, y) pair, got {value!r}")
    x, y = value
    for name, coordinate in (("x", x), ("y", y)):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise InvalidCardGeometry(
                f"a corner's {name} must be a real number, got {type(coordinate).__name__}"
            )
        if not math.isfinite(float(coordinate)):
            raise InvalidCardGeometry(f"a corner's {name} must be finite, got {coordinate!r}")
    return (float(x), float(y))


def _validated_dimension(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCardGeometry(f"{label} must be a whole number of pixels, got {value!r}")
    if value <= 0:
        raise InvalidCardGeometry(f"{label} must be positive, got {value!r}")
    return value


def _validated_detector(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidCardGeometry("detector must be a non-empty identifier")
    if "latest" in value.lower():
        # The refusal `catalog_version.py` and `image_quality.py` both make, for
        # the reason the project's versioning invariant gives: a record naming
        # "latest" says nothing about what actually ran.
        raise InvalidCardGeometry(f"detector must name a fixed version, not {value!r}")
    return value.strip()


def _cross(origin: Corner, first: Corner, second: Corner) -> float:
    """The z component of ``(first - origin) x (second - origin)``."""
    return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
        second[0] - origin[0]
    )


def _distance(start: Corner, end: Corner) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


@dataclass(frozen=True, slots=True)
class CardGeometry:
    """A detected card boundary — spec §18, #37.

    Args:
        corners: Four ``(x, y)`` points in the original photograph's
            coordinates, **clockwise from the top left**. Validated rather than
            trusted; see the module docstring.
        confidence: How sure the detector is that this is a card. A
            :class:`~tcg_domain.confidence.Confidence` so that it cannot arrive
            as an 87 that meant 87%.
        frame_width: The original photograph's width in pixels.
        frame_height: The original photograph's height in pixels.
        detector: The version of the detector that produced this. Recorded so a
            historical verdict names what actually ran; never "latest".
        candidates: How many card-like quadrilaterals the detector found in the
            frame, this one included. More than one is spec §19's
            `multiple_cards`.
        enclosing_ratio: The area of the smallest plausible enclosing
            quadrilateral — a sleeve or a top-loader — as a multiple of this
            one's. Exactly ``1.0`` means none was found, which is the answer for
            a bare card.
        thresholds: The values the detector ran with, copied on construction.
            Carried for the same reason
            :attr:`~tcg_domain.image_quality.QualityReport.thresholds` is: they
            are a parameter, so a stored verdict that named only the version
            would not explain itself to a reader who cannot know what was passed
            at the time.

    Raises:
        InvalidCardGeometry: If the quadrilateral is not four finite corners
            running clockwise around a convex shape, or a field is out of range.
    """

    corners: tuple[Corner, Corner, Corner, Corner]
    confidence: Confidence
    frame_width: int
    frame_height: int
    detector: str
    candidates: int = 1
    enclosing_ratio: float = 1.0
    thresholds: Mapping[str, float] = _NO_THRESHOLDS

    def __post_init__(self) -> None:
        set_field = object.__setattr__

        corners = tuple(_validated_corner(corner) for corner in self.corners)
        if len(corners) != 4:
            raise InvalidCardGeometry(f"a card boundary has four corners, got {len(corners)}")
        set_field(self, "corners", corners)

        set_field(self, "frame_width", _validated_dimension(self.frame_width, label="frame_width"))
        set_field(
            self, "frame_height", _validated_dimension(self.frame_height, label="frame_height")
        )
        set_field(self, "detector", _validated_detector(self.detector))

        if not isinstance(self.confidence, Confidence):
            raise InvalidCardGeometry(
                f"confidence must be a Confidence, got {type(self.confidence).__name__}"
            )

        if isinstance(self.candidates, bool) or not isinstance(self.candidates, int):
            raise InvalidCardGeometry(f"candidates must be a whole number, got {self.candidates!r}")
        if self.candidates < 1:
            raise InvalidCardGeometry(
                f"a geometry is itself a candidate, so candidates is at least 1, "
                f"got {self.candidates!r}"
            )

        ratio = self.enclosing_ratio
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise InvalidCardGeometry(
                f"enclosing_ratio must be a real number, got {type(ratio).__name__}"
            )
        if not math.isfinite(float(ratio)) or float(ratio) < 1.0:
            raise InvalidCardGeometry(
                f"an enclosing quadrilateral cannot be smaller than the card it encloses, "
                f"got {ratio!r}"
            )
        set_field(self, "enclosing_ratio", float(ratio))
        set_field(self, "thresholds", MappingProxyType(dict(self.thresholds)))

        self._require_a_clockwise_convex_quadrilateral()

    def _require_a_clockwise_convex_quadrilateral(self) -> None:
        """The corner-order guarantee, made structural.

        Convex *and* positively signed. Convexity alone would admit the four
        corners in the order top-left, bottom-left, bottom-right, top-right —
        a perfectly convex quadrilateral traversed the wrong way round, which
        mirrors the card and which no later stage could notice.
        """
        crosses = [
            _cross(
                self.corners[index], self.corners[(index + 1) % 4], self.corners[(index + 2) % 4]
            )
            for index in range(4)
        ]
        if any(cross <= 0.0 for cross in crosses):
            raise InvalidCardGeometry(
                "the four corners must run clockwise from the top left around a convex "
                f"quadrilateral, got {self.corners!r}"
            )

    # -- the measurements spec §19's geometric five are judged from ----------

    @property
    def area(self) -> float:
        """The quadrilateral's area in square pixels, by the shoelace formula."""
        total = sum(
            self.corners[index][0] * self.corners[(index + 1) % 4][1]
            - self.corners[(index + 1) % 4][0] * self.corners[index][1]
            for index in range(4)
        )
        return abs(total) / 2.0

    @property
    def frame_area(self) -> float:
        """The whole photograph's area in square pixels."""
        return float(self.frame_width * self.frame_height)

    @property
    def area_fraction(self) -> float:
        """How much of the frame the card fills — §19's `insufficient_card_size`.

        A fraction rather than a pixel count, because "big enough to grade from"
        is about how much of the sensor the card got, not about the megapixels
        of the file. Resolution is `low_resolution`'s question and is measured
        separately.
        """
        return self.area / self.frame_area

    @property
    def side_lengths(self) -> tuple[float, float, float, float]:
        """``(top, right, bottom, left)`` in pixels.

        Perspective correction sizes its output from these, which is the other
        reason the corner order is validated rather than assumed.
        """
        return (
            _distance(self.corners[0], self.corners[1]),
            _distance(self.corners[1], self.corners[2]),
            _distance(self.corners[2], self.corners[3]),
            _distance(self.corners[3], self.corners[0]),
        )

    @property
    def opposite_side_ratio(self) -> float:
        """How far from a rectangle this is — §19's `severe_perspective_distortion`.

        The worse of the two opposite-side ratios, always at least 1. A card
        photographed square-on has parallel opposite edges of equal length; one
        photographed at an angle has a near edge measurably longer than its far
        edge, and the ratio between them is the distortion, independent of how
        big the card is in the frame or how it is rotated.
        """
        top, right, bottom, left = self.side_lengths
        return max(
            max(top, bottom) / min(top, bottom),
            max(left, right) / min(left, right),
        )

    @property
    def border_margin_fraction(self) -> float:
        """The least corner-to-frame-edge gap — §19's `card_partly_outside_frame`.

        As a fraction of the frame's short edge, so it means the same thing at
        every resolution. Zero means a corner sits on the frame boundary, which
        is what a clipped card looks like: the detector cannot see the part that
        is missing, so the boundary it finds runs along the edge of the picture.
        Clamped at zero — a corner reported outside the frame is still just
        "against the edge", and a negative margin would invert the ordering the
        gate's thresholds rely on.
        """
        gaps = [
            gap
            for x, y in self.corners
            for gap in (x, y, self.frame_width - x, self.frame_height - y)
        ]
        return max(0.0, min(gaps)) / float(min(self.frame_width, self.frame_height))

    def __str__(self) -> str:
        return (
            f"card at {self.area_fraction:.0%} of frame, {self.confidence} confident "
            f"({self.detector})"
        )
