"""Predict a PSA grade distribution from a condition assessment — issue #223.

Spec §24's first per-company model, and the first executable statement of §2.2:
condition is measured once, company-independently (#186), and the mapping from
condition to *grade* is where the companies disagree. There is no universal
``condition_score → grade`` function here and there must never be one — there
are three, and this is PSA's.

What this is, exactly
---------------------
ADR 0011 decision 1: a **versioned deterministic mapping whose spread is
declared rather than fitted**. Nothing is trained, because `grading_outcomes`
holds zero rows; nothing reads a published tolerance, because a company's
standard is copyrighted text this repository does not reproduce. Each axis of
the assessment yields a damage fraction in ``[0, 1]``; each declared weight says
how many ladder steps a fully damaged axis costs at PSA; the weighted sum walks
a centre position down from the top of :data:`~tcg_grading_companies.companies.PSA_SCALE`;
and a Gaussian in **ladder steps** spreads the mass around it::

    evidence = top - sum(weight * damage)
    centre   = (1 - unmeasured) · evidence + unmeasured · ignorance_centre
    sigma    = base_sigma + unmeasured_sigma · unmeasured_share
    P(g[i])  ∝ exp(-(i - centre)^2 / (2 * sigma^2))

The blend on the second line is the one non-obvious term, and it is there
because an unmeasured axis contributes **no damage** — absence of evidence is
never evidence of absence. Without the blend, a card nobody could measure would
find no damage anywhere and answer *"most likely a PSA 10, very wide"*, which is
#91's "not measured is never 0%" committed in the optimistic direction and
exactly the confidently-wrong output the product exists to refuse. So the centre
walks toward a declared position on the ladder as coverage falls: at full
coverage it is the evidence's, at zero coverage it is the prior's, and the
widening happens on top of both.

**A thin assessment widens; it never refuses.** ADR 0011 decision 1 again:
there is no coverage gate and no confidence gate on the prediction step, and an
assessment with every axis refused is still an assessment — it produces a very
wide distribution rather than a refusal. The only refusal in this step is a
refusal *on the way in*, and that never reaches here: a top-level
``{"insufficient_information": reason}`` in `analyses.condition_details` is
propagated by the caller (#227), which never builds a `ConditionAssessment` from
it. The return type stays :data:`~tcg_domain.confidence.Uncertain` because that
is the port's, and because a later trained model may have something this one
does not: a reason to decline.

**Every key is a point on PSA's own ladder — all eighteen, no bucket.** ADR 0011
decision 4. Buckets stay legal in `GradeDistribution`, on `GradeScale.supports`
and in `market_observations`; V1's predictors simply do not emit one, because
equal mass over the tail grades says exactly what ``7_or_lower`` says in the
vocabulary the outcomes table, the market ladder and #222's benchmark all
already speak.

Two members of the assessment are deliberately not axes
-------------------------------------------------------
``manufacturing_defects`` is *derived* from surface and edges by #186's
composer — the same findings, re-collected — so weighing it would count them
twice. ``eye_appeal`` is :class:`~tcg_domain.confidence.InsufficientInformation`
always and by construction (#180), so weighing it would add a constant to every
card and change nothing but the arithmetic's honesty.

`GradeDistribution`'s constructor **is** spec §63, so nothing here re-validates
``0 ≤ P(g) ≤ 1`` or ``Σ P(g) ≈ 1``. :meth:`GradeScale.validate` on the way out is
a different claim — that every key is a grade PSA can actually issue — and it
means a distribution naming 9.5 fails in this package rather than at the API.

Synchronous, like every analyzer and for `port.py`'s stated reason: it runs in a
Celery task, which is not the API's event loop and is exactly where blocking
belongs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

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
from tcg_grading_companies.companies import PSA_SCALE
from tcg_grading_companies.port import GradePrediction

from tcg_ml_grading_psa.thresholds import (
    DEFAULT_PSA_GRADING_THRESHOLDS,
    GRADING_PSA_VERSION,
    PSAGradingThresholds,
)

__all__ = ["predict"]


@dataclass(frozen=True, slots=True)
class _Axis:
    """One axis read twice: how damaged it looked, and how much nobody saw.

    Both in ``[0, 1]``, and they are independent. A refused axis is
    ``damage=0.0`` — absence of evidence, never evidence of absence — and
    ``unmeasured=1.0``, which is what widens the answer instead.
    """

    damage: float
    unmeasured: float


def predict(
    condition: ConditionAssessment,
    *,
    thresholds: PSAGradingThresholds = DEFAULT_PSA_GRADING_THRESHOLDS,
) -> Uncertain[GradePrediction]:
    """Predict PSA's grade distribution for one assessed card.

    Args:
        condition: Spec §13's neutral condition representation. A refused axis
            is a legal input and widens the answer; see the module docstring.
        thresholds: The declared constants. Replacing them wholesale is
            supported and is what an experiment does; shipping different ones
            means bumping :data:`GRADING_PSA_VERSION`.

    Returns:
        A :class:`~tcg_grading_companies.port.GradePrediction` over every point
        on PSA's eighteen-grade ladder. **This version never returns
        :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION`** — ADR 0011
        decision 1 puts the only refusal on the way in, where the caller
        propagates it without ever building an assessment.
    """
    axes = (
        (thresholds.centering_weight_steps, _centering_axis(condition.centering, thresholds)),
        (
            thresholds.corners_weight_steps,
            _region_axis(
                condition.corners,
                regions_per_side=len(CornerRegion),
                saturation=thresholds.corners_saturation_damage,
                thresholds=thresholds,
            ),
        ),
        (
            thresholds.edges_weight_steps,
            _region_axis(
                condition.edges,
                regions_per_side=len(EdgeRegion),
                saturation=thresholds.edges_saturation_damage,
                thresholds=thresholds,
            ),
        ),
        (thresholds.surface_weight_steps, _surface_axis(condition.surface, thresholds)),
    )

    ladder = PSA_SCALE.ordered
    top = float(len(ladder) - 1)
    evidence = min(top, max(0.0, top - math.fsum(weight * axis.damage for weight, axis in axes)))

    # Weight-weighted, so both the blend and the widening track what the missing
    # evidence was worth rather than how many mappings happened to refuse.
    unmeasured = math.fsum(weight * axis.unmeasured for weight, axis in axes) / math.fsum(
        weight for weight, _ in axes
    )
    centre = (1.0 - unmeasured) * evidence + unmeasured * top * thresholds.unmeasured_centre_ratio
    sigma = thresholds.base_sigma_steps + thresholds.unmeasured_sigma_steps * unmeasured

    masses = [
        math.exp(-((index - centre) ** 2) / (2.0 * sigma * sigma)) for index in range(len(ladder))
    ]
    total = math.fsum(masses)
    distribution = GradeDistribution(
        {grade: mass / total for grade, mass in zip(ladder, masses, strict=True)}
    )
    PSA_SCALE.validate(distribution)

    return GradePrediction(
        grade_probability=distribution,
        # ADR 0011 decision 1's two bounds: never more certain than the evidence
        # read, and never more certain than an uncalibrated mapping is entitled
        # to be. It is not the distribution's spread — a distribution can be
        # narrow and wrong, and `expected_value` takes this as
        # `distribution_confidence`, so conflating them reaches the economics.
        model_confidence=Confidence(min(condition.confidence.value, thresholds.confidence_ceiling)),
        model_version=GRADING_PSA_VERSION,
    )


def _damage(severity: DefectSeverity | None, thresholds: PSAGradingThresholds) -> float:
    """What one finding of this severity contributes to its axis's damage sum.

    ``None`` is the domain's spelling for a finding that claims no defect —
    ``clean``, and ``unknown`` where the analyzer saw a region it could not
    judge. Neither is damage.

    ponytail: an `unknown` *surface* finding is a defect nobody could name
    rather than a region nobody could see, and this scores it zero on both
    counts. Unreachable in v0.1.0 — `ml/surface`'s baseline emits stain and
    scuff only — and the fix when it is reachable is a third reading on
    `_Axis`, not a guessed severity.
    """
    if severity is DefectSeverity.MINOR:
        return thresholds.minor_damage
    if severity is DefectSeverity.MODERATE:
        return thresholds.moderate_damage
    if severity is DefectSeverity.SEVERE:
        return thresholds.severe_damage
    return 0.0


def _centering_axis(centering: Uncertain[Centering], thresholds: PSAGradingThresholds) -> _Axis:
    """Centering's two readings.

    The four ratios are weighted rather than averaged: front and back
    tolerances are not the same at PSA, and a per-ratio refusal drops out of
    the mean and into `unmeasured` instead of being scored as a zero offset.
    """
    if isinstance(centering, InsufficientInformation):
        return _Axis(damage=0.0, unmeasured=1.0)

    front = thresholds.front_centering_share / 2.0
    back = (1.0 - thresholds.front_centering_share) / 2.0
    shares = (
        ("front_horizontal", front),
        ("front_vertical", front),
        ("back_horizontal", back),
        ("back_vertical", back),
    )

    measured = 0.0
    damage = 0.0
    for name, share in shares:
        ratio = getattr(centering, name)
        if isinstance(ratio, InsufficientInformation):
            continue
        # A ratio is `near / (near + far)`, 0.5 perfect (`ml/centering`), so the
        # offset runs 0 (dead centre) to 1 (no border on one side at all).
        offset = abs(ratio - 0.5) * 2.0
        damage += share * min(1.0, offset / thresholds.centering_full_penalty_offset)
        measured += share
    if measured == 0.0:
        # `Centering` refuses construction with all four ratios refused, so this
        # is the domain's guarantee restated rather than a case that arrives —
        # and it is a division, which is not a guard to leave off.
        return _Axis(damage=0.0, unmeasured=1.0)
    return _Axis(damage=damage / measured, unmeasured=1.0 - measured)


def _region_axis[R](
    sides: Mapping[ImageSide, Uncertain[Mapping[R, RegionFinding]]],
    *,
    regions_per_side: int,
    saturation: float,
    thresholds: PSAGradingThresholds,
) -> _Axis:
    """Corners' or edges' two readings — one function, because the two axes
    differ only in their region vocabulary and their saturation.

    A region labelled ``unknown`` is one the analyzer could not judge
    (`ml/corners`: *"a corner it cannot judge is `unknown`, never a guessed
    `clean`"*), so it widens rather than scoring — which is
    :func:`~tcg_domain.condition.region_coverage`'s rule, counted in the domain
    since #225 so that the three predictors count once and weigh three times.
    A refused side is all four of its regions at once.
    """
    damage = 0.0
    for side in V1_SIDES:
        findings = sides[side]
        if isinstance(findings, InsufficientInformation):
            continue
        for finding in findings.values():
            damage += _damage(finding.severity, thresholds)
    return _Axis(
        damage=min(1.0, damage / saturation),
        unmeasured=1.0 - region_coverage(sides, regions_per_side=regions_per_side),
    )


def _surface_axis(
    sides: Mapping[ImageSide, Uncertain[SurfaceAssessment]], thresholds: PSAGradingThresholds
) -> _Axis:
    """Surface's two readings.

    ``not_assessed`` is the coverage signal and it is load-bearing: ADR 0010's
    fine classes are refused class-level by the v0.1.0 baseline, so a surface
    side that answered still leaves most of §16's vocabulary unlooked-at, and
    :func:`~tcg_domain.condition.surface_coverage` says so instead of reading an
    empty `findings` tuple as a clean card.
    """
    damage = 0.0
    for side in V1_SIDES:
        face = sides[side]
        if isinstance(face, InsufficientInformation):
            continue
        damage += math.fsum(_damage(defect.severity, thresholds) for defect in face.findings)
    return _Axis(
        damage=min(1.0, damage / thresholds.surface_saturation_damage),
        unmeasured=1.0 - surface_coverage(sides),
    )
