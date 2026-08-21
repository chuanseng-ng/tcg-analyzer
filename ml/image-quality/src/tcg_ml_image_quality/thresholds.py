"""The numbers the gate runs on — issue #36.

The issue is explicit that these must be "configurable and recorded, not magic
numbers buried in the code, because M7 needs to compare against this baseline".
Both halves are here: a frozen dataclass a caller may replace wholesale, and
:meth:`QualityThresholds.as_record`, whose output is written into
`images.quality_details` beside :data:`IMAGE_QUALITY_VERSION` on every
assessment.

**They are a parameter, not environment configuration.** Twenty
`TCG_API_QUALITY_*` variables and fourteen matching `.env.example` stanzas would
be machinery for values no deployment tunes independently — and would leave the
record ambiguous anyway, since a reader of an old row could not tell what the
variables were set to at the time. Recording the effective values per image is
what M7 actually needs; a caller that wants different ones passes a different
:class:`QualityThresholds`.

**Changing a value means bumping the version.** The identifier below names a
fixed set of thresholds, exactly as a model bundle names fixed weights. Two runs
that disagree must be distinguishable, so a threshold change is its own
`chore(ml/image-quality): ...` commit that moves the version with it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = ["DEFAULT_THRESHOLDS", "IMAGE_QUALITY_VERSION", "QualityThresholds"]

#: What produced a verdict. Recorded on every image; never a pointer to
#: "current", per the project's versioning invariant.
IMAGE_QUALITY_VERSION: Final = "image-quality-heuristic-v0.1.0"


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Three points on each of the six measurements this gate decides.

    `unusable` and `poor` are spec §19's two consequential lines. `ideal` is the
    third, and it is a fact about the measurement rather than a tuning knob: it
    is where the measurement stops getting better in any way that matters, which
    is what lets a score be a fraction instead of a distance in whatever units
    the heuristic happens to work in.

    Their order is the direction. `blur_variance` runs 40 → 120 → 500 because
    more Laplacian energy is sharper; `glare_area` runs 0.03 → 0.005 → 0.0
    because less reflection is better. Nothing declares which way is better —
    reading it off the numbers means the two cannot contradict each other.

    Raises:
        ValueError: If a triple is not strictly ordered, or a positive value is
            not positive.
    """

    #: The long edge the photograph is scaled down to before anything is
    #: measured. Load-bearing: **the blur thresholds below are calibrated
    #: against this size**, because Laplacian variance is not scale-invariant,
    #: and changing it without recalibrating them silently changes the gate.
    #: Downscaling only — a small photograph is never enlarged.
    work_long_edge: int = 1024

    # -- low resolution: the *original* short edge, before the downscale -----
    #: Below this there is nothing worth measuring at all.
    min_short_edge_unusable: int = 640
    #: Below this, corner and edge analysis has too few pixels to work with.
    min_short_edge_poor: int = 1000
    #: A phone photograph filling the frame with the card. More pixels than this
    #: are more pixels, not more card.
    min_short_edge_ideal: int = 2000

    # -- blur: variance of the Laplacian, at `work_long_edge` ----------------
    blur_variance_unusable: float = 40.0
    blur_variance_poor: float = 120.0
    blur_variance_ideal: float = 500.0

    # -- excessive darkness: mean luminance, 0-255 ---------------------------
    darkness_mean_unusable: float = 35.0
    darkness_mean_poor: float = 60.0
    #: Mid-grey. Both this and `brightness_mean_ideal` sit here because the two
    #: conditions are one measurement read from opposite ends.
    darkness_mean_ideal: float = 128.0

    # -- excessive brightness: mean luminance, 0-255 -------------------------
    brightness_mean_unusable: float = 225.0
    brightness_mean_poor: float = 200.0
    brightness_mean_ideal: float = 128.0

    # -- poor exposure: the 5th-to-95th percentile spread --------------------
    #: Deliberately not the same thing as dark or bright. This is a photograph
    #: with a normal average and a compressed histogram — flat, hazy light, or a
    #: phone that has fought a backlit scene to a draw.
    exposure_range_unusable: float = 30.0
    exposure_range_poor: float = 60.0
    exposure_range_ideal: float = 200.0

    # -- glare: the largest blob of near-saturated pixels --------------------
    #: What counts as blown out, 0-255.
    glare_level: int = 250
    #: The largest *connected* saturated region, as a fraction of the frame.
    #: A fraction of all saturated pixels would be the wrong measure: a white
    #: card border is saturated and is not glare, while one specular blob on the
    #: holo layer is.
    glare_area_unusable: float = 0.03
    glare_area_poor: float = 0.005
    #: No reflection at all, which is both the ideal and the floor.
    glare_area_ideal: float = 0.0

    def __post_init__(self) -> None:
        if self.work_long_edge <= 0:
            raise ValueError(f"work_long_edge must be positive, got {self.work_long_edge!r}")
        if not 0 < self.glare_level <= 255:
            raise ValueError(f"glare_level must lie in (0, 255], got {self.glare_level!r}")

        for name, unusable, poor, ideal in self.limits():
            ascending = unusable < poor < ideal
            descending = unusable > poor > ideal
            if not (ascending or descending):
                raise ValueError(
                    f"{name}: the unusable threshold must be stricter than the poor one and the "
                    f"ideal better than both, got unusable={unusable!r} poor={poor!r} "
                    f"ideal={ideal!r}"
                )

    def limits(self) -> tuple[tuple[str, float, float, float], ...]:
        """Each condition's three points, worst first.

        Written out rather than derived from the field names: a naming
        convention that decides whether an image is refused is a naming
        convention somebody will break by renaming a field.
        """
        return (
            (
                "low_resolution",
                float(self.min_short_edge_unusable),
                float(self.min_short_edge_poor),
                float(self.min_short_edge_ideal),
            ),
            (
                "blur",
                self.blur_variance_unusable,
                self.blur_variance_poor,
                self.blur_variance_ideal,
            ),
            (
                "excessive_darkness",
                self.darkness_mean_unusable,
                self.darkness_mean_poor,
                self.darkness_mean_ideal,
            ),
            (
                "excessive_brightness",
                self.brightness_mean_unusable,
                self.brightness_mean_poor,
                self.brightness_mean_ideal,
            ),
            (
                "poor_exposure",
                self.exposure_range_unusable,
                self.exposure_range_poor,
                self.exposure_range_ideal,
            ),
            ("glare", self.glare_area_unusable, self.glare_area_poor, self.glare_area_ideal),
        )

    def as_record(self) -> dict[str, float]:
        """The form persisted beside every verdict, so a row explains itself."""
        return {name: float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`IMAGE_QUALITY_VERSION` names them.
DEFAULT_THRESHOLDS: Final = QualityThresholds()
