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
CARD_DETECTION_VERSION: Final = "card-detection-opencv-v0.10.0"

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

    #: How far a corner may sit outside an enclosing quadrilateral, in pixels
    #: **at :attr:`work_long_edge`**, and still count as inside it (#206). A
    #: phantom that shares the card's own edges — the corpus's text panel —
    #: fits its corners a couple of pixels either side of the winning
    #: quadrilateral's (2.8 px, measured); a pixel count rather than a fraction
    #: because the jitter is the edge ribbon's wall thickness plus the fit's
    #: error, which does not scale with the card.
    containment_slack_px: float = 6.0

    #: How far outside the card a quadrilateral must sit **on every one of its
    #: four sides**, as a fraction of the card's own long edge, before it is a
    #: holder rather than another extraction pass's opinion of the same edge.
    #: 0.035 of 88 mm is about 3 mm.
    #:
    #: A fraction of the card and not a pixel count (#207): the thing it must
    #: exclude scales with the card, because it *is* the card — over the
    #: corpus's 28 real photographs the six passes placed the same boundary up
    #: to 3.0 mm apart, reading the drop shadow, the Otsu region or the
    #: saturation map instead of a second object, and 2.31 mm was the largest
    #: any of those stood off on all four sides at once. The old pixel floor
    #: (7 px at :attr:`work_long_edge`) is 0.68 mm at the corpus's ~10 px/mm,
    #: an order of magnitude under what it was meant to exclude, and it let
    #: 21 of 28 bare cards report a sleeve.
    #:
    #: **Every side, not an average**: the corpus's enclosing quadrilaterals
    #: stood off as unevenly as 0.0 / 6.0 / 4.4 / -0.1 mm, because a shadow
    #: falls on one side and a holder surrounds the card. An area ratio cannot
    #: see the difference; four standoffs can.
    #:
    #: The cost is stated rather than hidden: **a sleeve is not resolvable and
    #: is not reported**. A penny sleeve stands off ~1.6 mm, which is inside
    #: the disagreement above — and was already invisible before this issue, at
    #: any threshold. What this condition detects is a rigid holder. Lowering
    #: this number to "restore" sleeve sensitivity restores the false positive
    #: instead; the way to move it is to photograph sleeved and top-loadered
    #: cards and measure, which no corpus yet supports.
    sleeve_standoff_fraction: float = 0.035
    #: And a quadrilateral enclosing the card by more than this is the table, a
    #: mat or a mount. A top-loader sits near 1.3.
    sleeve_max_ratio: float = 1.50

    #: A quadrilateral containing a card-shaped one, and itself below this
    #: aspect, is a case around a card rather than the card (#320). A grader's
    #: slab — PSA, BGS and CGC share 3.25 x 5.25 inches — is 0.619; a card is
    #: 0.716, a top-loader 0.75. Measured on real photographs: the one slab
    #: that returned a quadrilateral, 0.615; the corpus's 28 bare cards, every
    #: containing quadrilateral 0.691-0.720 and every winner 0.683-0.743. This
    #: sits near the middle of that gap.
    #:
    #: **It refuses only a container.** A bare card tilted until its own
    #: aspect falls below this line is untouched unless something card-shaped
    #: is found inside it and is closer to a card's proportions than it is —
    #: which is also required, and which a real tilt defeated (#325; see
    #: :attr:`case_max_perspective_ratio`). What is not measured is a slab
    #: photographed at an angle, and a PSA or TAG slab. The one real slab is
    #: refused on its card (0.742 inside the shell's 0.6147, a margin of
    #: 0.075); from v0.7.0 until #327 its card chained into the shell's group
    #: and the refusal rested on the label, by 0.0012.
    case_max_aspect: float = 0.65
    #: A container keystoned past this opposite-side ratio is not tested as a
    #: case at all (#325): an aspect read through that much perspective is the
    #: tilt's, not the object's. On real tilted bare cards the card
    #: foreshortened to 0.604 and 0.610 and its own text region to 0.611 and
    #: 0.628 — "nearer a card" by noise — and both were refused as a case, at
    #: skew 1.504 and 1.640. The one real slab's shell reads 1.040.
    #:
    #: **The same number as `tcg_ml_image_quality`'s
    #: `perspective_ratio_unusable`, and it must stay so.** Exempting only what
    #: the gate refuses on perspective anyway means this can change a refusal's
    #: reason and never turn a refusal into an answer. A copy rather than an
    #: import, because neither ML package imports the other.
    case_max_perspective_ratio: float = 1.45
    #: A returned quadrilateral below :attr:`case_max_aspect` whose own group
    #: holds a member nearer a card's proportions, at no more than this
    #: fraction of its area, is a case whose card grouped with it (#330). A
    #: card over a slab is physically 0.504. On angled TAG slabs the card
    #: measured 0.464-0.712 of the returned shell; bare cards in the same
    #: domain (tilted, skew under 1.45) measured 0.966 and 0.980, and no
    #: corpus card reaches the domain at all.
    case_max_area_ratio: float = 0.75
    #: A returned quadrilateral below this aspect, read square-on (skew no
    #: more than :attr:`case_square_on_max_perspective_ratio`), is a case
    #: whose card was never found at all (#332). Measured from the corners:
    #: square-on PSA shells 0.566 and 0.584, and a close-up's panel through a
    #: case 0.598; the corpus's 28 winners 0.670 and up; #322's synthetic tilts
    #: still rated `good` or `poor` at that skew, 0.641 and up. This sits in
    #: that gap.
    #:
    #: **Not a floor at any skew.** Warned synthetic tilts at skew up to 1.45
    #: read as low as 0.509 (#322), level with the angled PSA and TAG slabs
    #: this does not reach (0.509-0.523). Those are left to the perspective
    #: warning.
    case_square_on_max_aspect: float = 0.62
    #: The skew a quadrilateral may carry and still have its aspect read as
    #: the object's (#332). **The same number as `tcg_ml_image_quality`'s
    #: `perspective_ratio_poor`**: past it the gate warns of perspective, and
    #: the aspect is the tilt's as much as the object's. A copy, as with
    #: :attr:`case_max_perspective_ratio`.
    case_square_on_max_perspective_ratio: float = 1.12

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
        if not 0.0 < self.sleeve_standoff_fraction < 1.0:
            raise ValueError(
                "sleeve_standoff_fraction must lie in (0, 1), got "
                f"{self.sleeve_standoff_fraction!r}"
            )
        if not 0.0 < self.case_max_aspect < CARD_ASPECT:
            raise ValueError(
                f"case_max_aspect must lie in (0, {CARD_ASPECT:.3f}), got {self.case_max_aspect!r}"
            )
        if self.case_max_perspective_ratio <= 1.0:
            raise ValueError(
                f"case_max_perspective_ratio must exceed 1, got {self.case_max_perspective_ratio!r}"
            )
        if not 0.0 < self.case_max_area_ratio < 1.0:
            raise ValueError(
                f"case_max_area_ratio must lie in (0, 1), got {self.case_max_area_ratio!r}"
            )
        if not 0.0 < self.case_square_on_max_aspect <= self.case_max_aspect:
            raise ValueError(
                "case_square_on_max_aspect must lie in (0, case_max_aspect], got "
                f"{self.case_square_on_max_aspect!r}"
            )
        if not 1.0 < self.case_square_on_max_perspective_ratio <= self.case_max_perspective_ratio:
            raise ValueError(
                "case_square_on_max_perspective_ratio must lie in "
                f"(1, case_max_perspective_ratio], got {self.case_square_on_max_perspective_ratio!r}"
            )
        if self.containment_slack_px < 0.0:
            raise ValueError(
                f"containment_slack_px must not be negative, got {self.containment_slack_px!r}"
            )
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
