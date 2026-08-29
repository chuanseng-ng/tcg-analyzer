"""The numbers the detector runs on — issue #37.

`ml/image-quality/thresholds.py` is the model, and the reasoning transfers
whole: a frozen dataclass a caller may replace wholesale rather than twenty
`TCG_API_*` variables no deployment tunes independently, and
:meth:`DetectionThresholds.as_record` so that a stored verdict explains itself
without anybody knowing what was configured at the time.

**The record's keys are prefixed, and that is not decoration.** The gate merges
this record into the one it writes to `images.quality_details`, and both
dataclasses have a `work_long_edge`. Prefixing here rather than at the merge
means the two cannot collide however the merge is written.

**Changing a value means bumping the version**, exactly as it does for the gate.
:data:`CARD_DETECTION_VERSION` names a fixed set of numbers the way a model
bundle names fixed weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "CARD_ASPECT",
    "CARD_DETECTION_VERSION",
    "DEFAULT_DETECTION_THRESHOLDS",
    "DetectionThresholds",
]

#: What located a card. Recorded on every image the detector ran against; never
#: a pointer to "current", per the project's versioning invariant.
CARD_DETECTION_VERSION: Final = "card-detection-opencv-v0.3.0"

#: A trading card is 63 x 88 mm, so its short edge is this fraction of its long
#: one. The acceptance band around it is wide because perspective shortens one
#: axis and because a sleeve or a top-loader has its own proportions.
CARD_ASPECT: Final = 63.0 / 88.0


@dataclass(frozen=True, slots=True)
class DetectionThresholds:
    """What counts as a card, and what counts as a sleeve around one.

    Raises:
        ValueError: If a bound is not positive, or a band is not ordered.
    """

    #: The long edge the photograph is scaled down to before anything is
    #: measured. Contour extraction on a 48-megapixel photograph is both slow
    #: and worse — sensor noise becomes contours. Corners are scaled back into
    #: the original's coordinates before they leave this package.
    work_long_edge: int = 1024

    #: Below this fraction of the frame a quadrilateral is a sticker, a logo or
    #: a floor tile, not the card the photograph is of. Deliberately far below
    #: the gate's `insufficient_card_size` line: a card too small to analyse is
    #: something the gate must *refuse*, which it cannot do if this filter has
    #: already thrown the card away.
    min_area_fraction: float = 0.02
    #: Above this it is the frame itself — a border, a mount, or the contour of
    #: the whole picture.
    max_area_fraction: float = 0.92

    #: The accepted range of short-edge-over-long-edge. Centred on
    #: :data:`CARD_ASPECT` (0.716) and generous, because the ratio measured from
    #: a photograph is foreshortened by however the card was tilted.
    min_aspect: float = 0.45
    max_aspect: float = 0.97

    #: Contour area over the area of the quadrilateral fitted to it. A card is a
    #: filled rectangle, so the two nearly agree; a hand, a shadow or a pile of
    #: cards fits a quadrilateral badly.
    min_rectangularity: float = 0.80

    #: `approxPolyDP`'s tolerance, as a fraction of the contour's perimeter.
    approx_epsilon: float = 0.02

    #: Two candidates whose centres are within this fraction of the frame's
    #: short edge are the same card — found twice by two extraction passes, or
    #: found once inside its sleeve. Getting this wrong reports one card as two,
    #: which the gate calls `multiple_cards` and refuses the photograph for.
    duplicate_centre_fraction: float = 0.06

    #: How far outside the card a quadrilateral must sit, in pixels **at
    #: :attr:`work_long_edge`**, before it is a sleeve rather than an artifact.
    #: A pixel floor rather than an area ratio because the artifact it excludes
    #: is one: the inner and outer walls of a closed edge ribbon are a fixed few
    #: pixels apart whatever size the card is, so a ratio floor would be too
    #: tight for a small card and too loose for a large one.
    sleeve_min_margin: float = 7.0
    #: And a quadrilateral enclosing the card by more than this is the table, a
    #: mat or a mount. A penny sleeve sits near 1.05 and a top-loader near 1.4.
    sleeve_max_ratio: float = 1.50

    #: A corner within this fraction of the frame's short edge of the frame
    #: boundary counts as touching it — the same normalisation, and the same
    #: number, as the gate's `border_margin_poor`, because they describe the
    #: same situation from two sides.
    frame_margin_fraction: float = 0.005
    #: A quadrilateral that both touches the frame boundary and covers at least
    #: this fraction of the frame is the picture's own boundary, not a card —
    #: #176's shadow-merged close-ups produced exactly that shape and it was
    #: returned at 76-82% confidence. A clipped card touches the boundary too,
    #: which is why the fill condition is required as well.
    frame_fill_fraction: float = 0.70

    def __post_init__(self) -> None:
        if self.work_long_edge <= 0:
            raise ValueError(f"work_long_edge must be positive, got {self.work_long_edge!r}")
        if not 0.0 < self.min_rectangularity <= 1.0:
            raise ValueError(
                f"min_rectangularity must lie in (0, 1], got {self.min_rectangularity!r}"
            )
        if not 0.0 < self.approx_epsilon < 1.0:
            raise ValueError(f"approx_epsilon must lie in (0, 1), got {self.approx_epsilon!r}")
        if self.sleeve_min_margin <= 0.0:
            raise ValueError(f"sleeve_min_margin must be positive, got {self.sleeve_min_margin!r}")
        if not 0.0 < self.frame_margin_fraction < 1.0:
            raise ValueError(
                f"frame_margin_fraction must lie in (0, 1), got {self.frame_margin_fraction!r}"
            )
        if not 0.0 < self.frame_fill_fraction <= 1.0:
            raise ValueError(
                f"frame_fill_fraction must lie in (0, 1], got {self.frame_fill_fraction!r}"
            )

        for name, low, high in (
            ("area fraction", self.min_area_fraction, self.max_area_fraction),
            ("aspect", self.min_aspect, self.max_aspect),
            ("sleeve ratio", 1.0, self.sleeve_max_ratio),
        ):
            if not 0.0 < low < high:
                raise ValueError(
                    f"the {name} band must be an ordered pair of positive numbers, "
                    f"got {low!r} and {high!r}"
                )

    def as_record(self) -> dict[str, float]:
        """The form merged into the quality report's thresholds.

        Prefixed, so it cannot collide with the gate's own record — see the
        module docstring.
        """
        return {f"card_detection_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`CARD_DETECTION_VERSION` names them.
DEFAULT_DETECTION_THRESHOLDS: Final = DetectionThresholds()
