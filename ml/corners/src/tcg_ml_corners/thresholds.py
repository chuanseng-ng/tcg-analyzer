"""The numbers the corner classification runs on — issue #183.

`ml/centering/thresholds.py` is the model, and the reasoning transfers whole:
a frozen dataclass a caller may replace wholesale rather than a dozen
`TCG_API_*` variables no deployment tunes independently, and
:meth:`CornerThresholds.as_record` so a stored classification explains itself
without anybody knowing what was configured at the time.

**The record's keys are prefixed, and that is not decoration.** Whatever
record this is merged into also carries the detector's, the normalizer's and
the centering measurement's thresholds, and prefixing here rather than at the
merge means none of them can collide however the merge is written.

**Changing a value means bumping the version**, exactly as it does for the
detector. :data:`CORNERS_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.

Pixel fields are denominated in artifact pixels because the artifact's scale
is fixed by construction (12 px/mm) — `min_axis_border_px`'s reasoning. The
severity boundaries are denominated in mm² so they read as physical claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "CORNERS_VERSION",
    "DEFAULT_CORNER_THRESHOLDS",
    "CornerThresholds",
]

#: What classified a corner. Recorded beside every finding this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. Epic #8's decision 5: a heuristic's version is a code constant,
#: never a registry row.
CORNERS_VERSION: Final = "corners-opencv-v0.1.0"


@dataclass(frozen=True, slots=True)
class CornerThresholds:
    """What counts as whitening at a corner, and what refuses.

    Raises:
        ValueError: If a bound is out of range, a band is not ordered, or the
            bands do not fit inside the corner crop.
    """

    #: The corner crop's side, in artifact pixels: 7 mm at 12 px/mm — the
    #: region ADR 0010's contact sheet judged corner extent from. Also the
    #: corner/edge boundary: `ml/edges` excludes exactly this much from each
    #: end of every edge run (`corner_exclusion_px`, #184) — change one side
    #: of the mirror and the other, or a defect at the seam is
    #: double-reported or dropped.
    corner_size_px: int = 84

    #: How deep the whitening detection band runs along each card edge
    #: (1 mm): exposed core shows at the very edge, and the same L-shaped
    #: band one step deeper is the printed border sampled as a reference.
    edge_band_px: int = 12

    #: The outermost pixels skipped entirely: residual normalization slop
    #: plus the anti-aliased blend between card and background at the
    #: physical edge (~0.17 mm) — background bleed must not read as core.
    edge_inset_px: int = 2

    #: The nominal corner-cut arc (~2.5 mm). Inside the tip square the card
    #: ends at this arc, and everything beyond it is photographed background
    #: — near-white on a white surface, and never damage.
    corner_radius_px: int = 30

    #: The HSV ceilings and floors for "near-white": exposed paper core is
    #: achromatic and bright, where every printed border except a white one
    #: is saturated or dark.
    max_white_saturation: int = 60
    min_white_value: int = 190

    #: The reference-band near-white fraction at or above which the printed
    #: border itself is white, whitening is indistinguishable in this
    #: signal, and the corner answers `unknown` rather than guessing.
    white_border_fraction: float = 0.5

    #: The severity bands, in mm² of near-white inside the detection band.
    #: At or below the floor is `clean` — 0.25 mm² is 36 px, the despeckled
    #: noise floor; past it, minor until `moderate_min_area_mm2`, moderate
    #: until `severe_min_area_mm2`, severe beyond.
    #: ponytail: physical priors, not measurements — #188's evaluation
    #: against `image_annotations` severities is what calibrates them, and
    #: changing one bumps CORNERS_VERSION.
    clean_max_area_mm2: float = 0.25
    moderate_min_area_mm2: float = 1.5
    severe_min_area_mm2: float = 4.0

    def __post_init__(self) -> None:
        if self.corner_size_px <= 0:
            raise ValueError(f"corner_size_px must be positive, got {self.corner_size_px!r}")
        if self.edge_band_px <= 0:
            raise ValueError(f"edge_band_px must be positive, got {self.edge_band_px!r}")
        if not 0 <= self.edge_inset_px < self.edge_band_px:
            raise ValueError(
                f"edge_inset_px must lie in [0, edge_band_px), "
                f"got {self.edge_inset_px!r} against {self.edge_band_px!r}"
            )
        if self.edge_inset_px + 2 * self.edge_band_px > self.corner_size_px:
            raise ValueError("the detection and reference bands must fit inside the corner crop")
        if not 0 < self.corner_radius_px <= self.corner_size_px:
            raise ValueError(
                f"corner_radius_px must lie in (0, corner_size_px], got {self.corner_radius_px!r}"
            )
        for name in ("max_white_saturation", "min_white_value"):
            value = getattr(self, name)
            if not 0 < value <= 255:
                raise ValueError(f"{name} must lie in (0, 255], got {value!r}")
        if not 0.0 < self.white_border_fraction < 1.0:
            raise ValueError(
                f"white_border_fraction must lie in (0, 1), got {self.white_border_fraction!r}"
            )
        if (
            not 0.0
            < self.clean_max_area_mm2
            < self.moderate_min_area_mm2
            < self.severe_min_area_mm2
        ):
            raise ValueError(
                "the severity bands must be an ordered chain of positive areas, got "
                f"{self.clean_max_area_mm2!r}, {self.moderate_min_area_mm2!r} "
                f"and {self.severe_min_area_mm2!r}"
            )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the classification.

        Prefixed, so it cannot collide with any sibling package's record —
        see the module docstring.
        """
        return {f"corners_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`CORNERS_VERSION` names them.
DEFAULT_CORNER_THRESHOLDS: Final = CornerThresholds()
