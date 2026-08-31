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
IMAGE_QUALITY_VERSION: Final = "image-quality-heuristic-v0.3.0"


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Three points on each of the eleven measurements this gate decides.

    `unusable` and `poor` are spec §19's two consequential lines. `ideal` is the
    third, and it is a fact about the measurement rather than a tuning knob: it
    is where the measurement stops getting better in any way that matters, which
    is what lets a score be a fraction instead of a distance in whatever units
    the heuristic happens to work in.

    Their order is the direction. `blur_variance` runs 40 → 120 → 500 because
    more Laplacian energy is sharper; `glare_area` runs 0.08 → 0.0055 → 0.0
    because less reflection is better. Nothing declares which way is better —
    reading it off the numbers means the two cannot contradict each other.

    The first five are measured from the frame alone. The last six need the card
    boundary #37's detector supplies, and are simply not judged when no boundary
    was found — see :func:`~tcg_ml_image_quality.assess`.

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

    # -- glare: the largest reflecting region of the card --------------------
    #: How far inside the detected quadrilateral the measurement begins, as a
    #: fraction of the card's long edge. **Load-bearing, and measured** (#208):
    #: the card's own printed border carries a chroma gradient of its own, and
    #: #207 measured the detector's extraction passes disagreeing about where
    #: the card edge is by up to 3.0 mm. Below ~0.04 (3.5 mm) the separation
    #: between reflecting and clean photographs collapses; it holds across
    #: 0.04-0.12, and this sits in the middle of that plateau at about 5.3 mm.
    glare_card_inset_fraction: float = 0.06
    #: What counts as blown out, 0-255. A specular highlight bright enough to
    #: clip is glare too, and folding it into the same mask is what keeps one
    #: number able to say so.
    glare_level: int = 250
    #: How many times the card's *own* median chroma gradient a region must
    #: carry to count as reflecting. Relative rather than absolute because the
    #: quantity being detected is a reflection off this card, and an absolute
    #: level separates nothing: measured, it ranks foil finishes rather than
    #: reflections.
    glare_sweep_multiple: float = 5.0
    #: The largest *connected* reflecting region, as a fraction of the measured
    #: card region. Connected rather than total, and the distinction is the
    #: whole heuristic: scattered white ink is not glare, one sheet of returned
    #: light across the artwork is.
    #:
    #: Both lines come from #208's measurement over the corpus's 28 real
    #: photographs. `poor` is the geometric mean of the closest reflecting
    #: photograph (0.0102) and the worst clean one (0.0030) — a 3.4x gap.
    #: `unusable` is twice the largest value any real photograph produced, so
    #: **no photograph the annotator was willing to use can be refused**; a
    #: reflection has to cover an eighth of the card to reach it.
    glare_area_unusable: float = 0.08
    glare_area_poor: float = 0.0055
    #: No reflection at all, which is both the ideal and the floor.
    glare_area_ideal: float = 0.0

    # -- severe perspective distortion: opposite-side length ratio -----------
    #: 1.0 is a photograph taken square-on. A card held at an angle has a near
    #: edge measurably longer than its far edge, and the ratio between them is
    #: the distortion — independent of how large the card is in the frame and of
    #: how it is rotated. Perspective correction can undo a mild one; past the
    #: unusable line the far edge has too few pixels left to correct back.
    perspective_ratio_unusable: float = 1.45
    perspective_ratio_poor: float = 1.12
    perspective_ratio_ideal: float = 1.0

    # -- card partly outside frame: least corner-to-edge gap -----------------
    #: As a fraction of the frame's short edge. Exactly 0 means a corner sits on
    #: the picture's boundary, which is what a clipped card looks like from the
    #: inside: the detector cannot see the part that is missing, so the boundary
    #: it finds runs along the edge of the photograph.
    border_margin_unusable: float = 0.0
    border_margin_poor: float = 0.005
    #: A couple of per cent of clear space on every side, which is also what
    #: perspective correction needs to work with.
    border_margin_ideal: float = 0.02

    # -- multiple cards: how many card-like quadrilaterals were found --------
    #: A count, judged by the same machinery as everything else so there is one
    #: way a condition becomes a finding. **Any** second card is unusable: the
    #: analysis would not know which card the user is asking about, and picking
    #: one is exactly the confidently-wrong output spec §2.7 forbids.
    card_count_unusable: float = 2.0
    card_count_poor: float = 1.5
    card_count_ideal: float = 1.0

    # -- sleeve obstruction: the enclosing quadrilateral's area ratio --------
    #: 1.0 is a bare card, and a top-loader sits near 1.3; the detector reports
    #: nothing above 1.5, so **this condition is a `poor` finding by
    #: construction** — the unusable line sits where a "sleeve" would no longer
    #: be a sleeve, and exists so that the triple declares its direction the way
    #: every other one does.
    #:
    #: **A sleeve thinner than a rigid holder never reaches this line at all**,
    #: and that is the detector's limit rather than this one's (#207): a penny
    #: sleeve stands off ~1.6 mm, and on real photographs `ml/card-detection`'s
    #: six extraction passes disagree about where the card's own edge is by up
    #: to 3.0 mm. So the number arriving here is a rigid holder or it is 1.0.
    #: This line stayed at 1.02 through that fix on purpose: the measurement was
    #: wrong, not the line, and moving it would have traded one wrong warning
    #: for another.
    sleeve_ratio_unusable: float = 2.0
    sleeve_ratio_poor: float = 1.02
    sleeve_ratio_ideal: float = 1.0

    # -- insufficient card size: the card's share of the frame ---------------
    #: How much of the sensor the card actually got, which is a different
    #: question from how many megapixels the file has — that is
    #: `low_resolution`'s, and it is measured separately.
    card_area_unusable: float = 0.06
    card_area_poor: float = 0.15
    #: A card filling most of a portrait frame, which is what the upload screen
    #: asks for.
    card_area_ideal: float = 0.45

    def __post_init__(self) -> None:
        if self.work_long_edge <= 0:
            raise ValueError(f"work_long_edge must be positive, got {self.work_long_edge!r}")
        if not 0 < self.glare_level <= 255:
            raise ValueError(f"glare_level must lie in (0, 255], got {self.glare_level!r}")
        if not 0.0 <= self.glare_card_inset_fraction < 0.5:
            raise ValueError(
                "glare_card_inset_fraction must lie in [0, 0.5), got "
                f"{self.glare_card_inset_fraction!r}"
            )
        if self.glare_sweep_multiple <= 1.0:
            raise ValueError(
                "glare_sweep_multiple must exceed 1, or every card is reflecting, got "
                f"{self.glare_sweep_multiple!r}"
            )

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
            (
                "severe_perspective_distortion",
                self.perspective_ratio_unusable,
                self.perspective_ratio_poor,
                self.perspective_ratio_ideal,
            ),
            (
                "card_partly_outside_frame",
                self.border_margin_unusable,
                self.border_margin_poor,
                self.border_margin_ideal,
            ),
            (
                "multiple_cards",
                self.card_count_unusable,
                self.card_count_poor,
                self.card_count_ideal,
            ),
            (
                "sleeve_obstruction",
                self.sleeve_ratio_unusable,
                self.sleeve_ratio_poor,
                self.sleeve_ratio_ideal,
            ),
            (
                "insufficient_card_size",
                self.card_area_unusable,
                self.card_area_poor,
                self.card_area_ideal,
            ),
        )

    def as_record(self) -> dict[str, float]:
        """The form persisted beside every verdict, so a row explains itself."""
        return {name: float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`IMAGE_QUALITY_VERSION` names them.
DEFAULT_THRESHOLDS: Final = QualityThresholds()
