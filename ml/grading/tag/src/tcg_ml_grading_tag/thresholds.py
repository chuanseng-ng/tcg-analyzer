"""The numbers the TAG grade prediction runs on — spec §24, issue #224.

`ml/grading/psa/thresholds.py` is the structural model — a frozen dataclass a
caller may replace wholesale, and :meth:`TAGGradingThresholds.as_record` so a
stored prediction explains itself — and **the numbers are not**. They are not
PSA's constants retuned; they are constants of a different kind, because TAG
grades differently.

PSA's weights are denominated in **ladder steps**: a human eye ranks the axes,
and a fully damaged one costs a stated number of positions on the ladder. TAG is
a machine. `packages/grading-companies`' own reading of
``https://taggrading.com/pages/scale`` (2026-08-24) records the mechanism:

    TAG additionally scores on a 1-to-1000 point scale that it maps onto these
    [eighteen grades] […] its scale table lists nineteen rows for eighteen
    grades: 1 through 9 with a half point at every level, then 10 twice, as Gem
    Mint (score 950-989) and Pristine (990-1000).

So TAG's constants are denominated in **score points out of 1000**, a category's
sub-score is what survives its deductions, and the ladder is reached through a
**band table** rather than by walking steps. The bands are not uniform — the top
of the ladder occupies a sliver of the range and the bottom occupies most of it
— which is what makes the score-to-grade placement non-linear, and is the whole
reason this package is not PSA's with different numbers. `predictor.py`'s module
docstring states the shape.

**TAG's published band table is not reproduced here**, and it is not
reconstructed either. It is that company's reference data, for the same reason
`reference.py` sets ``rules = EMPTY_RULES`` on every record; and the citation
above gives one anchor — where the two grade-10 rows begin — which is nowhere
near enough to fit seventeen edges against. So the curve below is this project's
own declared prior, and its curvature is chosen for the behaviour it produces
rather than to approximate a table nobody here has read in full. That it is
non-uniform is the claim; the particular widths are not.

**Changing a value means bumping the version**, exactly as it does for an
analyzer. :data:`GRADING_TAG_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.

Every number below is a **declared prior, not a fitted parameter** — ADR 0011's
whole point rather than an apology for it. `grading_outcomes` holds zero rows.
The spread these constants produce is the model's declaration of how little it
knows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "DEFAULT_TAG_GRADING_THRESHOLDS",
    "GRADING_TAG_VERSION",
    "TAG_SCORE_MAXIMUM",
    "TAGGradingThresholds",
]

#: What predicted a TAG grade. Recorded beside every prediction this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. ADR 0011 decision 6: a baseline's version is a code constant,
#: never a `model_bundles` row — a row names a trained artifact with a dataset
#: version and metrics.
#:
#: The `heuristic` infix is `grading-psa-heuristic-v0.1.0`'s, and it is
#: load-bearing for the same reason: spec §59's grammar reserves
#: `grading-tag-v0.2.0` for the *trained* bundle that replaces this one, and the
#: two must not collide in `analyses.model_bundle_version`.
GRADING_TAG_VERSION: Final = "grading-tag-heuristic-v0.1.0"

#: The top of TAG's machine scale. Not a threshold — it is the unit every number
#: in :class:`TAGGradingThresholds` is denominated in, and moving it would
#: rescale the model rather than tune it.
TAG_SCORE_MAXIMUM: Final = 1000.0


@dataclass(frozen=True, slots=True)
class TAGGradingThresholds:
    """What each category scores out of 1000, and where a score meets a grade.

    Raises:
        ValueError: If a weight is not positive, the deduction chain is not
            ordered, or a score or fraction is out of range.
    """

    #: How much of the overall score each scanned category carries. **All four
    #: are equal, and that is a declared position rather than an omission.** PSA
    #: ranks its axes — a chipped corner is the defect a grader's eye reaches
    #: first — and its weights say so (corners 5, surface 4, centering 3, edges
    #: 3). TAG scans four categories with one instrument on one scale, and this
    #: project holds no measurement that would justify preferring one of them.
    #: That PSA and TAG therefore disagree about *which* damage matters is a
    #: consequence, and `tests/test_grade_predictors_differ.py` asserts it
    #: rather than assuming it.
    centering_weight: float = 1.0
    corners_weight: float = 1.0
    edges_weight: float = 1.0
    surface_weight: float = 1.0

    #: What one finding of each severity deducts from its category's sub-score.
    #: `DefectSeverity` is an ordinal by decision (#158), and turning it into a
    #: number is a modelling choice made where the model lives — here, in points
    #: rather than in PSA's dimensionless damage units. There is no saturation
    #: constant: a sub-score is **clamped at zero**, which is what a scored scale
    #: does and what a normalised damage fraction cannot.
    minor_deduction: float = 40.0
    moderate_deduction: float = 110.0
    severe_deduction: float = 220.0

    #: What a fully off-centre card loses from its centering sub-score. Not the
    #: whole 1000: a badly centred card is still a card, and TAG scores the
    #: category rather than condemning it.
    centering_full_deduction: float = 700.0

    #: The border offset at which centering deducts in full. A ratio is
    #: ``near / (near + far)`` with 0.5 perfect (`ml/centering`), so the offset
    #: is ``abs(ratio - 0.5) * 2`` and 0.40 is roughly a 70/30 split.
    #:
    #: **Front and back are weighted equally**, and there is deliberately no
    #: `front_centering_share` here. PSA's exists because a human grader holds
    #: the two faces to different tolerances; a scanner does not know which face
    #: it is looking at.
    centering_tolerance_offset: float = 0.40

    #: The band table's shape, and the most load-bearing number in this file.
    #: Band ``k``'s upper edge is
    #: ``TAG_SCORE_MAXIMUM * (1 - (1 - k/n) ** band_curvature)`` over the ``n``
    #: grades of TAG's ladder. At 1.0 the bands are uniform and the mapping is
    #: linear — which would make this predictor PSA's in different units. Above
    #: 1.0 they tighten toward the top: the last grades occupy a sliver of the
    #: range and the first occupy most of it, so a point of damage near the top
    #: of the ladder costs more grades than the same point near the bottom. That
    #: is the published mechanism's defining property, and it is why this mapping
    #: cannot be reached from PSA's by any choice of weights.
    #:
    #: 1.25 gives bands running 69 points wide at the bottom of the ladder down
    #: to 27 at the top. It was chosen against three behaviours rather than
    #: against TAG's table: a flawlessly scanned card peaks on 10 (at 1.5 it
    #: peaks on 9, because the top band shrinks to 13 points and the truncation
    #: at 1000 caps grade 10 near a third of the mass however clean the card);
    #: :attr:`unmeasured_score` lands mid-ladder; and the non-uniformity stays
    #: plain at 2.6 to 1.
    #:
    #: ponytail: a one-parameter curve standing in for a measured band table.
    #: The upgrade path is an explicit seventeen-edge tuple validated strictly
    #: increasing, declared once `grading_outcomes` holds rows to check it
    #: against — never a copy of TAG's own published table.
    band_curvature: float = 1.25

    #: Where a card nobody could scan sits, **in score points**. The overall
    #: score is blended toward it in proportion to the share of the card no
    #: instrument covered, so that ignorance never reads as evidence of a
    #: pristine card: an uncovered category deducts nothing, so an unblended
    #: score would sit at 1000 and answer *"probably a Pristine 10"* — #91's "not
    #: measured is never 0%" committed in the optimistic direction.
    #:
    #: 620 rather than half the scale, because the band table is not linear: this
    #: is the score whose band sits near the middle of TAG's ladder, and the
    #: middle is the only honest position at zero information. Half the *scale*
    #: would be a claim about damage nobody measured.
    unmeasured_score: float = 620.0

    #: The spread of a **fully covered** prediction, in score points. It is not
    #: zero and must not become zero: an uncalibrated declared mapping is not
    #: entitled to a narrow answer even when every category was scanned.
    base_sigma_score: float = 30.0

    #: How much wider the spread gets at total ignorance, added in proportion to
    #: the uncovered share. ADR 0011 decision 1's "a thin assessment widens",
    #: expressed as a number.
    unmeasured_sigma_score: float = 200.0

    #: The ceiling on
    #: :attr:`~tcg_grading_companies.port.GradePrediction.model_confidence` —
    #: ADR 0011 decision 1's second bound, beside the assessment's own
    #: confidence. It shares PSA's value because it is the ADR's rule about what
    #: an uncalibrated mapping may claim, not a property of TAG.
    confidence_ceiling: float = 0.35

    def __post_init__(self) -> None:
        for name in (
            "centering_weight",
            "corners_weight",
            "edges_weight",
            "surface_weight",
            "band_curvature",
            "base_sigma_score",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")
        if self.unmeasured_sigma_score < 0:
            raise ValueError(
                f"unmeasured_sigma_score must not be negative, got {self.unmeasured_sigma_score!r}"
            )
        for name in ("centering_full_deduction", "unmeasured_score"):
            value = getattr(self, name)
            if not 0.0 <= value <= TAG_SCORE_MAXIMUM:
                raise ValueError(f"{name} must lie in [0, {TAG_SCORE_MAXIMUM}], got {value!r}")
        for name in ("centering_tolerance_offset", "confidence_ceiling"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must lie in (0, 1], got {value!r}")
        if not 0.0 < self.minor_deduction < self.moderate_deduction < self.severe_deduction:
            raise ValueError(
                "the severity deductions must be an ordered chain of positive numbers, got "
                f"{self.minor_deduction!r}, {self.moderate_deduction!r} and "
                f"{self.severe_deduction!r}"
            )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the prediction.

        Prefixed, so it cannot collide with the four analyzers' records or with
        `grading_psa_`'s in the same stored document.
        """
        return {f"grading_tag_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`GRADING_TAG_VERSION` names them.
DEFAULT_TAG_GRADING_THRESHOLDS: Final = TAGGradingThresholds()
