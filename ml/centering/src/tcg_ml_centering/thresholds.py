"""The numbers the centering measurement runs on — issue #182.

`ml/card-detection/thresholds.py` is the model, and the reasoning transfers
whole: a frozen dataclass a caller may replace wholesale rather than a dozen
`TCG_API_*` variables no deployment tunes independently, and
:meth:`CenteringThresholds.as_record` so a stored measurement explains itself
without anybody knowing what was configured at the time.

**The record's keys are prefixed, and that is not decoration.** Whatever
record this is merged into also carries the detector's and the normalizer's
thresholds, and prefixing here rather than at the merge means the three cannot
collide however the merge is written.

**Changing a value means bumping the version**, exactly as it does for the
detector. :data:`CENTERING_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "CENTERING_VERSION",
    "DEFAULT_CENTERING_THRESHOLDS",
    "CenteringThresholds",
]

#: What measured a centering. Recorded beside every measurement this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. Epic #8's decision 5: a heuristic's version is a code constant,
#: never a registry row.
CENTERING_VERSION: Final = "centering-opencv-v0.1.0"


@dataclass(frozen=True, slots=True)
class CenteringThresholds:
    """What counts as a card's printed border frame, and what refuses.

    Raises:
        ValueError: If a bound is out of range, or a band is not ordered.
    """

    #: The accepted band of frame area as a fraction of the card's area. Below
    #: the floor the quadrilateral is an artwork window (a classic layout's is
    #: roughly 0.31 of the card) or a text box, not the border frame; above
    #: the ceiling it is the card's own edge ribbon re-detected, or a border
    #: too thin (under ~0.9 mm) to be one — §21's borderless case, refused.
    #: The classic yellow border sits near 0.85-0.90, a modern silver one near
    #: 0.89, and the back's blue frame near 0.77 — all inside the band.
    min_frame_area_fraction: float = 0.55
    max_frame_area_fraction: float = 0.95

    #: The accepted range of short-edge-over-long-edge for the frame. The
    #: content region of a conventional layout sits near 0.695; the band is
    #: generous because the area band does the real work, but a quadrilateral
    #: squarer than :attr:`max_aspect` is a window or a sticker, never a
    #: card's frame.
    min_aspect: float = 0.50
    max_aspect: float = 0.90

    #: Contour area over the area of the quadrilateral fitted to it. Tighter
    #: than the detector's 0.80, because the printed frame in a normalized
    #: artifact is a true rectangle — there is no perspective left to forgive.
    min_rectangularity: float = 0.85

    #: `approxPolyDP`'s tolerance, as a fraction of the contour's perimeter.
    approx_epsilon: float = 0.02

    #: A border wider than this fraction of the card's width is not a border:
    #: 0.15 of 63 mm is roughly 9.5 mm, past every real layout. A frame with
    #: one implies the quadrilateral is an artwork window, and the whole side
    #: refuses rather than trusting the other axis of a wrong quad (#176's
    #: lesson).
    max_border_fraction: float = 0.15

    #: The floor on an axis's border sum (its ratio's denominator), in
    #: artifact pixels — roughly 0.17 mm at 12 px/mm. At or below it the frame
    #: touches the card edge on that axis and there is no border to ratio:
    #: #160's zero-denominator refusal, kept and widened a hair. Denominated
    #: in pixels, like the detector's `sleeve_min_margin`, because the
    #: artifact's scale is fixed by construction.
    min_axis_border_px: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 < self.min_rectangularity <= 1.0:
            raise ValueError(
                f"min_rectangularity must lie in (0, 1], got {self.min_rectangularity!r}"
            )
        if not 0.0 < self.approx_epsilon < 1.0:
            raise ValueError(f"approx_epsilon must lie in (0, 1), got {self.approx_epsilon!r}")
        if not 0.0 < self.max_border_fraction < 1.0:
            raise ValueError(
                f"max_border_fraction must lie in (0, 1), got {self.max_border_fraction!r}"
            )
        if self.min_axis_border_px <= 0.0:
            raise ValueError(
                f"min_axis_border_px must be positive, got {self.min_axis_border_px!r}"
            )

        for name, low, high in (
            ("frame area fraction", self.min_frame_area_fraction, self.max_frame_area_fraction),
            ("aspect", self.min_aspect, self.max_aspect),
        ):
            if not 0.0 < low < high:
                raise ValueError(
                    f"the {name} band must be an ordered pair of positive numbers, "
                    f"got {low!r} and {high!r}"
                )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the measurement.

        Prefixed, so it cannot collide with the detector's or the
        normalizer's own records — see the module docstring.
        """
        return {f"centering_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`CENTERING_VERSION` names them.
DEFAULT_CENTERING_THRESHOLDS: Final = CenteringThresholds()
