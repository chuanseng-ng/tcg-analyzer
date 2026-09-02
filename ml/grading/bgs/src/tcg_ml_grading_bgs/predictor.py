"""Predict a BGS grade distribution from a condition assessment — issue #225.

Spec §24's third and last per-company model, and the one that proves the grade
scale is **per company rather than shared**. §24 ends with a sentence of its
own — *"BGS must support half grades"* — and BGS is where every assumption that
a grade is an integer, that the three companies share a ladder, or that "±1
grade" is arithmetic rather than a step on a scale, fails. It issues **nineteen**
grades where PSA and TAG issue eighteen, and the extra one is **9.5**.

What BGS does differently, and where it comes from
--------------------------------------------------
Beckett prints **four subgrades** — centering, corners, edges, surface — on the
same 1-to-10 half-point scale as the overall grade, and the overall grade is
**the worst of them**. `companies.py` already carries the sharpest statement of
that rule in this repository, as the definition of a designation: Black Label is
*"a BGS 10 whose four subgrades are each 10"*. Turned into arithmetic, that is::

    P(BGS 10) = Π_c P(subgrade_c = 10)

which is what this predictor computes, and it is why a BGS 10 is harder to reach
here than a PSA 10 is in `ml/grading/psa`.

So the three mappings are shaped differently at every stage::

    PSA (#223)  centre  = top - sum(weight * damage)      in LADDER STEPS, linear
                P(g[i]) ~= exp(-(i - centre)^2 / (2 * sigma^2))

    TAG (#224)  score   = sum(weight * subscore) / sum(weight)   out of 1000
                P(g)    = the score's mass in that grade's NON-UNIFORM band

    BGS (here)  sub[c]  = round(top - penalty[c])   in HALF GRADES, quantised
                P(g)    = the distribution of the MINIMUM of the four subgrades,
                          exactly: S(i) = prod_c P(sub[c] >= i), P(i) = S(i) - S(i+1)

Three properties follow, and none is reachable by retuning either sibling:

* **The aggregation is an order statistic, not a sum or a mean.** Wrecking a
  *second* category barely moves the answer, where PSA's weighted sum and TAG's
  weighted mean both fall again. `tests/test_grade_predictors_differ.py` asserts
  it on the same inputs.
* **The subgrade quantises before it is compared.** Beckett prints 9.5, never
  9.37, so a subgrade lands on the ladder and the answer moves in *steps* as
  damage grows continuously — both siblings keep a continuous centre.
* **The ignorance blend and the widening are per category**, because the minimum
  consumes the subgrades one at a time: ignorance about the corners must not
  widen the centering subgrade. Both siblings blend once, at the aggregate.

Damage **accumulates within** a category and the categories are **minimised
across** them. That asymmetry is BGS's rule rather than an inconsistency: a
second chipped corner makes the corner subgrade worse, and the overall grade is
the worst subgrade, so a second wrecked *category* does not.

**Nothing here is imported from `tcg_ml_grading_psa` or `tcg_ml_grading_tag`** —
not a type, not the severity mapping, not the normal density. ADR 0011: there is
no shared `ml/grading/common`, because a shared grading package is the universal
condition-to-grade mapping arriving by the back door. What the three genuinely
share is *counting*, which lives in `tcg_domain.condition` since #225.

What this is, exactly
---------------------
ADR 0011 decision 1: a **versioned deterministic mapping whose spread is
declared rather than fitted**. Nothing is trained, because `grading_outcomes`
holds zero rows; nothing reads a published tolerance, because a company's
standard is copyrighted text this repository does not reproduce — and
`companies.py` records that the BGS scale is the one entry in that package not
read from the company's own page. The constants in `thresholds.py` are this
project's own declared priors.

**A thin assessment widens; it never refuses.** There is no coverage gate and no
confidence gate on the prediction step, and an assessment with every category
refused is still an assessment. The only refusal in this step is a refusal *on
the way in*, and it never reaches here: a top-level
``{"insufficient_information": reason}`` in `analyses.condition_details` is
propagated by the caller (#227), which never builds a `ConditionAssessment` from
it. The return type stays :data:`~tcg_domain.confidence.Uncertain` because that
is the port's, and because a later trained model may have something this one does
not: a reason to decline.

**Every key is a point on BGS's own ladder — all nineteen, no bucket** (ADR 0011
decision 4), **and never a subgrade and never a designation.** The four subgrades
are this rule's internal working values and are surfaced by nothing: no route, no
record, no field. §24's output is a distribution over the overall grade, and
predicting four more distributions is a second product decision with its own
evaluation burden. Black Label is a label *on* grade 10, not a value on the
scale; this predictor answers ``10``.

`GradeDistribution`'s constructor **is** spec §63, so nothing here re-validates
``0 ≤ P(g) ≤ 1`` or ``Σ P(g) ≈ 1``. :meth:`GradeScale.validate` on the way out is
a different claim — that every key is a grade BGS can actually issue — and here
it is the claim the issue exists for: this is the one V1 scale on which 9.5
passes it.

Synchronous, like every analyzer and for `port.py`'s stated reason: it runs in a
Celery task, which is not the API's event loop.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.annotation import CornerRegion, DefectSeverity, EdgeRegion
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    RegionFinding,
    SurfaceAssessment,
    region_coverage,
    surface_coverage,
)
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain
from tcg_domain.distribution import GradeDistribution
from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade
from tcg_grading_companies.companies import BGS_SCALE
from tcg_grading_companies.port import GradePrediction

from tcg_ml_grading_bgs.thresholds import (
    DEFAULT_BGS_GRADING_THRESHOLDS,
    GRADING_BGS_VERSION,
    BGSGradingThresholds,
)

__all__ = ["predict"]


@dataclass(frozen=True, slots=True)
class _Subgrade:
    """One printed subgrade: where the evidence puts it, and how much evidence.

    ``position`` is a place on BGS's ladder — one position is one half grade —
    and ``coverage`` is a fraction in ``[0, 1]``; the two are independent. A
    category nobody could read keeps a **top** position and ``coverage=0.0``:
    absence of evidence is never evidence of damage, and the blend toward the
    declared ignorance subgrade is what stops it being read as evidence of
    quality either.
    """

    position: float
    coverage: float


def predict(
    condition: ConditionAssessment,
    *,
    thresholds: BGSGradingThresholds = DEFAULT_BGS_GRADING_THRESHOLDS,
) -> Uncertain[GradePrediction]:
    """Predict BGS's grade distribution for one assessed card.

    Args:
        condition: Spec §13's neutral condition representation. A refused
            category is a legal input and widens the answer; see the module
            docstring.
        thresholds: The declared constants. Replacing them wholesale is
            supported and is what an experiment does; shipping different ones
            means bumping :data:`GRADING_BGS_VERSION`.

    Returns:
        A :class:`~tcg_grading_companies.port.GradePrediction` over every point
        on BGS's nineteen-grade ladder — **9.5 included, which is the whole
        point** — and never a subgrade and never Black Label. **This version
        never returns :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION`** —
        ADR 0011 decision 1 puts the only refusal on the way in, where the caller
        propagates it without ever building an assessment.

    Raises:
        ValueError: If ``thresholds.unmeasured_subgrade`` is not a grade BGS can
            issue. Declared data, checked where the ladder is in scope.
    """
    ladder = BGS_SCALE.ordered
    top = float(len(ladder) - 1)
    ignorance = float(_ignorance_position(thresholds.unmeasured_subgrade, ladder))

    subgrades = (
        _centering_subgrade(condition.centering, thresholds, top=top),
        _region_subgrade(
            condition.corners,
            regions_per_side=len(CornerRegion),
            thresholds=thresholds,
            top=top,
        ),
        _region_subgrade(
            condition.edges, regions_per_side=len(EdgeRegion), thresholds=thresholds, top=top
        ),
        _surface_subgrade(condition.surface, thresholds, top=top),
    )

    masses = _worst_of(
        [
            _subgrade_masses(subgrade, thresholds, ignorance=ignorance, points=len(ladder))
            for subgrade in subgrades
        ]
    )
    total = math.fsum(masses)
    distribution = GradeDistribution(
        {grade: mass / total for grade, mass in zip(ladder, masses, strict=True)}
    )
    BGS_SCALE.validate(distribution)

    return GradePrediction(
        grade_probability=distribution,
        # ADR 0011 decision 1's two bounds: never more certain than the evidence
        # read, and never more certain than an uncalibrated mapping is entitled
        # to be. It is not the distribution's spread — a distribution can be
        # narrow and wrong, and `expected_value` takes this as
        # `distribution_confidence`, so conflating them reaches the economics.
        model_confidence=Confidence(min(condition.confidence.value, thresholds.confidence_ceiling)),
        model_version=GRADING_BGS_VERSION,
    )


def _ignorance_position(subgrade: float, ladder: Sequence[Grade]) -> int:
    """Where the declared ignorance subgrade sits on BGS's ladder.

    Looked up rather than computed from the ladder's step, which makes it a
    checked claim: a subgrade BGS could not print is not a position to blend
    toward. It is also the one place this module converts between a grade
    *value* and a ladder *position*, and the direction is deliberate — every
    other number in `thresholds.py` is already in positions, because a position
    on this ladder is a half grade.
    """
    try:
        return ladder.index(Grade(Decimal(str(subgrade))))
    except (InvalidGrade, ValueError) as error:
        raise ValueError(
            f"unmeasured_subgrade {subgrade!r} is not a grade BGS can issue; its scale is "
            f"{', '.join(str(grade) for grade in ladder)}"
        ) from error


def _subgrade_masses(
    subgrade: _Subgrade,
    thresholds: BGSGradingThresholds,
    *,
    ignorance: float,
    points: int,
) -> list[float]:
    """One subgrade's own distribution over the ladder.

    Two steps that are both BGS's rather than either sibling's:

    The blend toward the declared ignorance subgrade is **per category**, not
    once at the aggregate, because the minimum consumes the subgrades one at a
    time. It is not optional: an unread category loses nothing, so an unblended
    subgrade would sit at 10 and four of them would answer *"probably a Pristine
    10"* — #91's "not measured is never 0%" committed in the optimistic
    direction.

    Then the centre is **rounded onto the ladder**, because Beckett prints 9.5
    and never 9.37. That quantisation is what "half-step resolution" means here,
    and it is why this predictor's answer moves in steps where the other two
    slide.
    """
    centre = subgrade.coverage * subgrade.position + (1.0 - subgrade.coverage) * ignorance
    printed = float(round(centre))
    sigma = thresholds.base_sigma + thresholds.unmeasured_sigma * (1.0 - subgrade.coverage)
    weights = [
        math.exp(-((position - printed) ** 2) / (2.0 * sigma * sigma)) for position in range(points)
    ]
    total = math.fsum(weights)
    return [weight / total for weight in weights]


def _worst_of(subgrades: Sequence[Sequence[float]]) -> list[float]:
    """The distribution of the **minimum** of the four subgrades, exactly.

    ``P(min ≥ g) = Π_c P(subgrade_c ≥ g)`` for independent subgrades, and the
    masses telescope out of the survivals. No sampling, no approximation and —
    the point — **no soft minimum**: a smooth stand-in for ``min`` is a weighted
    mean wearing a different name, which is TAG's aggregation retuned rather
    than a third rule.

    At the top of the ladder this reads ``P(BGS 10) = Π_c P(subgrade_c = 10)``,
    which is `companies.py`'s Black Label definition. Unnormalised only against
    floating-point drift: the survivals telescope to exactly 1 in real
    arithmetic, and the caller divides by the total.
    """
    points = len(subgrades[0])
    survival = [
        math.prod(math.fsum(masses[position:]) for masses in subgrades)
        for position in range(points)
    ]
    survival.append(0.0)
    # `max(0.0, ...)` is not defensive: two nearly equal products differ by a
    # negative epsilon often enough, and `GradeDistribution` refuses a
    # probability outside [0, 1].
    return [max(0.0, higher - lower) for higher, lower in itertools.pairwise(survival)]


def _penalty(severity: DefectSeverity | None, thresholds: BGSGradingThresholds) -> float:
    """What one finding of this severity takes off its category's subgrade.

    ``None`` is the domain's spelling for a finding that claims no defect —
    ``clean``, and ``unknown`` where an analyzer saw a region it could not judge.
    Neither penalises; the second lowers coverage instead.
    """
    if severity is DefectSeverity.MINOR:
        return thresholds.minor_penalty
    if severity is DefectSeverity.MODERATE:
        return thresholds.moderate_penalty
    if severity is DefectSeverity.SEVERE:
        return thresholds.severe_penalty
    return 0.0


def _printed(top: float, penalty: float) -> float:
    """A subgrade is what survives its penalties, clamped at the bottom.

    The clamp is at grade 1, which is where BGS's scale starts — "0.5 increments"
    describes the step, not the floor (`companies.py`).
    """
    return max(0.0, top - penalty)


def _centering_subgrade(
    centering: Uncertain[Centering], thresholds: BGSGradingThresholds, *, top: float
) -> _Subgrade:
    """The centering subgrade, and how much of it was measured.

    The four ratios are averaged **unweighted**, as at TAG and unlike PSA, but
    for a different reason: BGS prints **one** centering subgrade, so the two
    faces are already one number by the time this rule sees them, and a
    `front_centering_share` would be deciding something the label does not.
    """
    if isinstance(centering, InsufficientInformation):
        return _Subgrade(position=top, coverage=0.0)

    ratios = ("front_horizontal", "front_vertical", "back_horizontal", "back_vertical")
    offsets = []
    for name in ratios:
        ratio = getattr(centering, name)
        if isinstance(ratio, InsufficientInformation):
            continue
        # A ratio is `near / (near + far)`, 0.5 perfect (`ml/centering`), so the
        # offset runs 0 (dead centre) to 1 (no border on one side at all).
        offset = abs(ratio - 0.5) * 2.0
        offsets.append(min(1.0, offset / thresholds.centering_tolerance_offset))
    if not offsets:
        # `Centering` refuses construction with all four ratios refused, so this
        # is the domain's guarantee restated rather than a case that arrives —
        # and it guards a division, which is not a guard to leave off.
        return _Subgrade(position=top, coverage=0.0)

    mean_offset = math.fsum(offsets) / len(offsets)
    return _Subgrade(
        position=_printed(top, thresholds.centering_full_penalty * mean_offset),
        coverage=len(offsets) / len(ratios),
    )


def _region_subgrade[R](
    sides: Mapping[ImageSide, Uncertain[Mapping[R, RegionFinding]]],
    *,
    regions_per_side: int,
    thresholds: BGSGradingThresholds,
    top: float,
) -> _Subgrade:
    """The corners' or the edges' subgrade — one function, because the two
    categories differ only in their region vocabulary.

    Penalties **accumulate**: Beckett prints one number for all four corners, so
    a second chipped corner does make that subgrade worse. It is only *across*
    categories that the rule stops adding and starts taking the worst.
    """
    penalty = 0.0
    for side in V1_SIDES:
        findings = sides[side]
        if isinstance(findings, InsufficientInformation):
            continue
        for finding in findings.values():
            penalty += _penalty(finding.severity, thresholds)
    return _Subgrade(
        position=_printed(top, penalty),
        coverage=region_coverage(sides, regions_per_side=regions_per_side),
    )


def _surface_subgrade(
    sides: Mapping[ImageSide, Uncertain[SurfaceAssessment]],
    thresholds: BGSGradingThresholds,
    *,
    top: float,
) -> _Subgrade:
    """The surface subgrade, and how much of §16's vocabulary was looked at.

    ``not_assessed`` is the coverage signal and it is load-bearing: ADR 0010's
    fine classes are refused class-level by the v0.1.0 baseline, so a surface
    side that answered still leaves most of §16's vocabulary unread, and
    :func:`~tcg_domain.condition.surface_coverage` says so instead of taking an
    empty `findings` tuple for a clean card.
    """
    penalty = 0.0
    for side in V1_SIDES:
        face = sides[side]
        if isinstance(face, InsufficientInformation):
            continue
        penalty += math.fsum(_penalty(defect.severity, thresholds) for defect in face.findings)
    return _Subgrade(position=_printed(top, penalty), coverage=surface_coverage(sides))
