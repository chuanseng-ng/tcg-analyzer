"""The numbers the surface classification runs on — issue #185.

`ml/edges/thresholds.py` is the model, and the reasoning transfers whole:
a frozen dataclass a caller may replace wholesale rather than a dozen
`TCG_API_*` variables no deployment tunes independently, and
:meth:`SurfaceThresholds.as_record` so a stored classification explains
itself without anybody knowing what was configured at the time.

**The record's keys are prefixed, and that is not decoration.** Whatever
record this is merged into also carries the detector's, the normalizer's,
the centering measurement's and both edge axes' thresholds, and prefixing
here rather than at the merge means none of them can collide however the
merge is written.

**Changing a value means bumping the version**, exactly as it does for the
detector. :data:`SURFACE_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.

Pixel fields are denominated in artifact pixels because the artifact's
scale is fixed by construction (12 px/mm) — `min_axis_border_px`'s
reasoning. The area boundaries are denominated in mm² so they read as
physical claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "DEFAULT_SURFACE_THRESHOLDS",
    "SURFACE_VERSION",
    "SurfaceThresholds",
]

#: What classified a surface. Recorded beside every finding this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. Epic #8's decision 5: a heuristic's version is a code
#: constant, never a registry row.
SURFACE_VERSION: Final = "surface-opencv-v0.1.0"


@dataclass(frozen=True, slots=True)
class SurfaceThresholds:
    """What counts as a stain or a scuff on the card face, and what refuses.

    Raises:
        ValueError: If a bound is out of range or the area bands are not an
            ordered chain.
    """

    #: The border strip surface never claims a candidate in: the edge and
    #: corner analyzers'
    #: detection and reference bands run to depth
    #: ``edge_inset_px + 2 * edge_band_px`` = 26 px (`ml/edges`, mirrored by
    #: `ml/corners`' L-bands), and near-white there is their whitening
    #: signal — reporting it here too would double-report one defect across
    #: two axes (#184's seam rule, extended to this axis). Convention on
    #: both doc-comments, not a checkable rule; the first importer of the
    #: packages asserts the mirror.
    border_exclusion_px: int = 26

    #: The grey ceiling for a stain candidate: a dark foreign mark (ink,
    #: grime) against a face that is not itself that dark.
    stain_max_value: int = 60

    #: The HSV ceilings and floors for a scuff candidate: a dull whitish
    #: abrasion is achromatic and bright — the same physical claim the edge
    #: and corner axes make about exposed core, and the same numbers.
    max_white_saturation: int = 60
    min_white_value: int = 190

    #: A pixel is *busy* where its 3x3 Laplacian magnitude exceeds this —
    #: artwork, print and foil sparkle as the baseline sees them.
    laplacian_threshold: int = 24

    #: The foil clause (#185): at or above this busy fraction over the whole
    #: face, defect texture is indistinguishable from the face's own, and
    #: `stain` and `scuff` are refused class-level rather than guessed.
    face_busy_fraction: float = 0.35

    #: The per-candidate context gate (#176's filter-before-selection): a
    #: candidate whose surrounding annulus — its box dilated by
    #: ``context_margin_px`` — is at or above this busy fraction sits inside
    #: artwork and is dropped.
    context_busy_fraction: float = 0.25
    context_margin_px: int = 12

    #: The area bands, in mm² of candidate blob. Below the floor is not
    #: reported — coarse classes are millimetre-scale, and a sub-millimetre
    #: mark is the fine classes' refused territory; past it, minor until
    #: `moderate_min_area_mm2`, moderate until `severe_min_area_mm2`,
    #: severe beyond.
    #: ponytail: physical priors, not measurements — #188's evaluation
    #: against `image_annotations` severities is what calibrates them, and
    #: changing one bumps SURFACE_VERSION.
    min_defect_area_mm2: float = 1.0
    moderate_min_area_mm2: float = 10.0
    severe_min_area_mm2: float = 25.0

    def __post_init__(self) -> None:
        for name in ("border_exclusion_px", "context_margin_px", "laplacian_threshold"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")
        for name in ("stain_max_value", "max_white_saturation", "min_white_value"):
            value = getattr(self, name)
            if not 0 < value <= 255:
                raise ValueError(f"{name} must lie in (0, 255], got {value!r}")
        for name in ("face_busy_fraction", "context_busy_fraction"):
            value = getattr(self, name)
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must lie in (0, 1), got {value!r}")
        if (
            not 0.0
            < self.min_defect_area_mm2
            < self.moderate_min_area_mm2
            < self.severe_min_area_mm2
        ):
            raise ValueError(
                "the area bands must be an ordered chain of positive areas, got "
                f"{self.min_defect_area_mm2!r}, {self.moderate_min_area_mm2!r} "
                f"and {self.severe_min_area_mm2!r}"
            )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the classification.

        Prefixed, so it cannot collide with any sibling package's record —
        see the module docstring.
        """
        return {f"surface_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`SURFACE_VERSION` names them.
DEFAULT_SURFACE_THRESHOLDS: Final = SurfaceThresholds()
