"""The numbers the PSA grade prediction runs on — spec §24, issue #223.

`ml/corners/thresholds.py` is the model, and the reasoning transfers whole: a
frozen dataclass a caller may replace wholesale rather than a dozen `TCG_API_*`
variables no deployment tunes independently, and
:meth:`PSAGradingThresholds.as_record` so a stored prediction explains itself
without anybody knowing what was configured at the time.

**The record's keys are prefixed, and that is not decoration.** Whatever record
this is merged into also carries the four axis analyzers' thresholds, and
prefixing here rather than at the merge means none of them can collide however
the merge is written.

**Changing a value means bumping the version**, exactly as it does for an
analyzer. :data:`GRADING_PSA_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.

Every number below is a **declared prior, not a fitted parameter**, and that is
ADR 0011's whole point rather than an apology for it. `grading_outcomes` holds
zero rows, PSA's published standard is copyrighted text this repository does not
reproduce (`rules = EMPTY_RULES`), and a mapping fitted to nothing would be
noise wearing a curve. The spread these constants produce is the model's
declaration of how little it knows.

Two numbers are deliberately **not** here. The ladder comes from
:data:`~tcg_grading_companies.companies.PSA_SCALE`, and §27's 80% target and its
Wilson bound live in `ml/evaluation` because they belong to ADR 0011 rather than
to any one predictor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "DEFAULT_PSA_GRADING_THRESHOLDS",
    "GRADING_PSA_VERSION",
    "PSAGradingThresholds",
]

#: What predicted a PSA grade. Recorded beside every prediction this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. ADR 0011 decision 6: a baseline's version is a code constant,
#: never a `model_bundles` row — a row names a trained artifact with a dataset
#: version and metrics, and null-filling those for code would make the registry
#: lie.
#:
#: The `heuristic` infix is `image-quality-heuristic-v0.3.0`'s, and it is
#: load-bearing: spec §59's own example `grading-psa-v0.2.0` names the *trained*
#: bundle that replaces this one, and the two must not collide in
#: `analyses.model_bundle_version`.
GRADING_PSA_VERSION: Final = "grading-psa-heuristic-v0.1.0"


@dataclass(frozen=True, slots=True)
class PSAGradingThresholds:
    """What each axis costs a card at PSA, and how wide the answer is.

    The four weights are denominated in **ladder steps** — positions on
    :attr:`~tcg_grading_companies.scale.GradeScale.ordered`, never points of
    grade value. PSA's ladder runs 1, 1.5 … 9, 10, so one step down from 10 is
    9 and one step down from 9 is 8.5; arithmetic on the value would get one of
    those wrong.

    Raises:
        ValueError: If a weight is not positive, a fraction is out of range, or
            the severity chain is not ordered.
    """

    #: What a fully off-centre card costs. Centering is the classic reason a
    #: submission comes back a 9 rather than a 10, and it is the one axis
    #: measured as a continuous quantity rather than counted as defects.
    #:
    #: ponytail: every constant in this class is a declared prior nobody
    #: measured — ADR 0011 decision 1 permits exactly that and nothing more.
    #: #222's harness against `grading_outcomes` is what would calibrate them,
    #: and moving any one bumps GRADING_PSA_VERSION.
    centering_weight_steps: float = 3.0

    #: What fully damaged corners cost. The heaviest weight: a chipped corner is
    #: the defect a grader's eye reaches first and the one a slab photograph
    #: shows most plainly.
    corners_weight_steps: float = 5.0

    #: What fully damaged edges cost.
    edges_weight_steps: float = 3.0

    #: What a fully damaged surface costs.
    surface_weight_steps: float = 4.0

    #: The border offset at which the centering axis reads fully damaged. A
    #: ratio is ``near / (near + far)`` with 0.5 perfect (`ml/centering`), so
    #: the offset here is ``abs(ratio - 0.5) * 2`` and 0.40 is roughly a 70/30
    #: split — past which a card is off-centre enough that being further off
    #: changes nothing a grader would score differently.
    centering_full_penalty_offset: float = 0.40

    #: How much of the centering axis the **front** carries. Front and back
    #: tolerances are not the same at PSA, and treating the two faces alike is
    #: the shortcut that would make this axis company-independent — which is
    #: exactly what spec §2.2 forbids.
    front_centering_share: float = 0.75

    #: What one finding of each severity contributes to its axis's damage sum.
    #: `DefectSeverity` is an ordinal by decision (#158), and turning it into a
    #: number is *"a modelling choice made where the model lives"* — here.
    minor_damage: float = 0.5
    moderate_damage: float = 1.5
    severe_damage: float = 3.0

    #: The summed damage at which an axis reads fully damaged. Six is two severe
    #: findings, or four moderate ones, out of the eight slots corners and edges
    #: each have — a card that far gone sits at the bottom of the ladder however
    #: much worse it gets.
    corners_saturation_damage: float = 6.0
    edges_saturation_damage: float = 6.0
    surface_saturation_damage: float = 6.0

    #: The spread of a **fully measured** prediction, in ladder steps. It is not
    #: zero and must not become zero: an uncalibrated declared mapping is not
    #: entitled to a narrow answer even when every axis answered.
    base_sigma_steps: float = 1.2

    #: How much wider the spread gets at total ignorance — an assessment whose
    #: every axis refused. Added in proportion to the weight-weighted share of
    #: evidence nobody looked at, which is ADR 0011 decision 1's "a thin
    #: assessment widens" expressed as a number.
    unmeasured_sigma_steps: float = 4.0

    #: Where on the ladder a card nobody could measure sits, as a fraction from
    #: the bottom to the top. The centre is blended toward it in proportion to
    #: the unmeasured share, so that **ignorance never reads as evidence of a
    #: pristine card**: with no damage found because nothing was looked at, an
    #: unblended centre would sit at the top and answer "probably a 10", which
    #: is #91's "not measured is never 0%" committed in the optimistic
    #: direction. 0.5 is the middle of PSA's ladder — a declared prior, and the
    #: only honest one at zero information.
    unmeasured_centre_ratio: float = 0.5

    #: The ceiling on
    #: :attr:`~tcg_grading_companies.port.GradePrediction.model_confidence` —
    #: ADR 0011 decision 1's second bound, beside the assessment's own
    #: confidence. No V1 prediction may present itself as more certain than an
    #: uncalibrated mapping is entitled to be, however clean the evidence.
    confidence_ceiling: float = 0.35

    def __post_init__(self) -> None:
        for name in (
            "centering_weight_steps",
            "corners_weight_steps",
            "edges_weight_steps",
            "surface_weight_steps",
            "corners_saturation_damage",
            "edges_saturation_damage",
            "surface_saturation_damage",
            "base_sigma_steps",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")
        if self.unmeasured_sigma_steps < 0:
            raise ValueError(
                f"unmeasured_sigma_steps must not be negative, got {self.unmeasured_sigma_steps!r}"
            )
        if not 0.0 <= self.unmeasured_centre_ratio <= 1.0:
            raise ValueError(
                f"unmeasured_centre_ratio must lie in [0, 1], got {self.unmeasured_centre_ratio!r}"
            )
        for name in ("centering_full_penalty_offset", "confidence_ceiling"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must lie in (0, 1], got {value!r}")
        if not 0.0 < self.front_centering_share < 1.0:
            raise ValueError(
                f"front_centering_share must lie in (0, 1), got {self.front_centering_share!r}"
            )
        if not 0.0 < self.minor_damage < self.moderate_damage < self.severe_damage:
            raise ValueError(
                "the severity damages must be an ordered chain of positive numbers, got "
                f"{self.minor_damage!r}, {self.moderate_damage!r} and {self.severe_damage!r}"
            )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the prediction.

        Prefixed, so it cannot collide with any sibling package's record — see
        the module docstring.
        """
        return {f"grading_psa_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`GRADING_PSA_VERSION` names them.
DEFAULT_PSA_GRADING_THRESHOLDS: Final = PSAGradingThresholds()
