"""Predict a TAG grade distribution from a condition assessment — issue #224.

Spec §24's second per-company model, and the one that has to prove §2.2 rather
than restate it. TAG's ladder is index-for-index identical to PSA's — eighteen
points, half steps from 1 to 9, then 10, and no 9.5 on either — so a TAG
predictor built as PSA's rule with different weights would be numerically
PSA-shaped for every input. That is the universal ``condition_score → grade``
mapping the architecture forbids, shipped under a second version string. **The
difference is in the rule, not in the constants.**

What TAG does differently, and where it comes from
--------------------------------------------------
`packages/grading-companies` already records the mechanism, read from
``https://taggrading.com/pages/scale`` on 2026-08-24: TAG is a machine grader
that scores on a **1-to-1000 point scale** and maps that score onto its
eighteen grades through a published table whose rows are **not evenly spaced** —
the two grade-10 designations between them occupy the top fifty points of a
thousand. PSA is a human eye ranking axes against one another.

So the two mappings are shaped differently at every stage::

    PSA (#223)  centre   = top - sum(weight * damage)      in LADDER STEPS, linear
                P(g[i]) ~= exp(-(i - centre)^2 / (2 * sigma^2))

    TAG (here)  subscore = clamp(1000 - sum(deductions), 0, 1000)
                score    = sum(weight * subscore) / sum(weight)
                edge[k]  = 1000 * (1 - (1 - k/n) ** curvature)   NON-UNIFORM bands
                P(g)     = [Phi((hi - score)/sigma) - Phi((lo - score)/sigma)] / Z

A category earns points and loses them; it does not accumulate a dimensionless
damage fraction against a saturation constant. The four categories are weighted
**equally**, because one instrument on one scale gives no reason to prefer one
(PSA's 5/4/3/3 is an eye's ranking, and its `front_centering_share` is a human
holding two faces to different tolerances — a scanner does not know which face
it is looking at). And the score reaches the ladder through **bands of unequal
width**, which makes the placement non-linear in aggregate damage.

That last property is the one no choice of PSA's weights can reproduce, and
`tests/test_grade_predictors_differ.py` asserts both consequences directly — that
PSA distinguishes a wrecked corner from a wrecked edge where TAG does not, and
that PSA walks the ladder in even steps where TAG's steps **shrink** as the card
gets worse, because the bands widen as they descend.

**Nothing here is imported from `tcg_ml_grading_psa`** — not the axis type, not
the severity mapping, not the normal density. ADR 0011: there is no shared
`ml/grading/common`, because a shared grading package is the universal
condition-to-grade mapping arriving by the back door.

What this is, exactly
---------------------
ADR 0011 decision 1: a **versioned deterministic mapping whose spread is
declared rather than fitted**. Nothing is trained, because `grading_outcomes`
holds zero rows; nothing reads a published tolerance, because a company's
standard — and its band table — is copyrighted text this repository does not
reproduce. The curve in `thresholds.py` is this project's own declared prior.

**A thin assessment widens; it never refuses.** There is no coverage gate and no
confidence gate on the prediction step, and an assessment with every category
refused is still an assessment. The only refusal in this step is a refusal *on
the way in*, and it never reaches here: a top-level
``{"insufficient_information": reason}`` in `analyses.condition_details` is
propagated by the caller (#227), which never builds a `ConditionAssessment` from
it. The return type stays :data:`~tcg_domain.confidence.Uncertain` because that
is the port's, and because a later trained model may have something this one does
not: a reason to decline.

**Every key is a point on TAG's own ladder — all eighteen, no bucket** (ADR 0011
decision 4), **and never a designation.** TAG issues grade 10 under two names,
Pristine and Gem Mint, and `companies.py` is explicit that *"the prediction side
does not read them and must not start"*: a designation is something a slab
already carries, and §24's output is a distribution over grades. This predictor
answers ``10``.

**ADR 0006's TAG refusal is about prices and stops at the market boundary.**
Market data for TAG is `insufficient_information` for all of V1; that is a
statement about what a provider sells, and letting it reach this package would
make TAG unrenderable for a reason spec §24 never asked for. The two are
unrelated.

Two members of the assessment are deliberately not categories
--------------------------------------------------------------
``manufacturing_defects`` is *derived* from surface and edges by #186's
composer — the same findings, re-collected — so scoring it would deduct for them
twice. ``eye_appeal`` is :class:`~tcg_domain.confidence.InsufficientInformation`
always and by construction (#180), so scoring it would deduct a constant from
every card.

`GradeDistribution`'s constructor **is** spec §63, so nothing here re-validates
``0 ≤ P(g) ≤ 1`` or ``Σ P(g) ≈ 1``. :meth:`GradeScale.validate` on the way out is
a different claim — that every key is a grade TAG can actually issue — and it
means a distribution naming 9.5 fails in this package rather than at the API.

Synchronous, like every analyzer and for `port.py`'s stated reason: it runs in a
Celery task, which is not the API's event loop.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
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
from tcg_grading_companies.companies import TAG_SCALE
from tcg_grading_companies.port import GradePrediction

from tcg_ml_grading_tag.thresholds import (
    DEFAULT_TAG_GRADING_THRESHOLDS,
    GRADING_TAG_VERSION,
    TAG_SCORE_MAXIMUM,
    TAGGradingThresholds,
)

__all__ = ["predict"]


@dataclass(frozen=True, slots=True)
class _Category:
    """One scanned category: what it scored, and how much of it was scanned.

    ``subscore`` is in score points on ``[0, TAG_SCORE_MAXIMUM]`` and
    ``coverage`` is a fraction in ``[0, 1]``; the two are independent. A category
    nobody could read keeps a **full** sub-score and ``coverage=0.0`` — absence
    of evidence is never evidence of damage, and the blend on the overall score
    is what stops it being read as evidence of quality either.

    Inverted from PSA's `_Axis`, and on purpose: a machine reports what a
    category scored, not what was deducted from it.
    """

    subscore: float
    coverage: float


def predict(
    condition: ConditionAssessment,
    *,
    thresholds: TAGGradingThresholds = DEFAULT_TAG_GRADING_THRESHOLDS,
) -> Uncertain[GradePrediction]:
    """Predict TAG's grade distribution for one assessed card.

    Args:
        condition: Spec §13's neutral condition representation. A refused
            category is a legal input and widens the answer; see the module
            docstring.
        thresholds: The declared constants. Replacing them wholesale is
            supported and is what an experiment does; shipping different ones
            means bumping :data:`GRADING_TAG_VERSION`.

    Returns:
        A :class:`~tcg_grading_companies.port.GradePrediction` over every point
        on TAG's eighteen-grade ladder, and never a designation. **This version
        never returns :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION`** —
        ADR 0011 decision 1 puts the only refusal on the way in, where the caller
        propagates it without ever building an assessment.
    """
    categories = (
        (thresholds.centering_weight, _centering_category(condition.centering, thresholds)),
        (
            thresholds.corners_weight,
            _region_category(
                condition.corners, regions_per_side=len(CornerRegion), thresholds=thresholds
            ),
        ),
        (
            thresholds.edges_weight,
            _region_category(
                condition.edges, regions_per_side=len(EdgeRegion), thresholds=thresholds
            ),
        ),
        (thresholds.surface_weight, _surface_category(condition.surface, thresholds)),
    )

    total_weight = math.fsum(weight for weight, _ in categories)
    scanned = (
        math.fsum(weight * category.coverage for weight, category in categories) / total_weight
    )
    measured = (
        math.fsum(weight * category.subscore for weight, category in categories) / total_weight
    )

    # The blend, and it is not optional. An uncovered category deducts nothing,
    # so an unblended score sits at 1000 for a card no instrument could read and
    # answers "probably a Pristine 10" — #91's rule broken in the optimistic
    # direction. At full coverage the score is the scan's; at zero coverage it is
    # the declared position's, and the widening happens on top of both.
    score = scanned * measured + (1.0 - scanned) * thresholds.unmeasured_score
    sigma = thresholds.base_sigma_score + thresholds.unmeasured_sigma_score * (1.0 - scanned)

    ladder = TAG_SCALE.ordered
    masses = _band_masses(score, sigma, _band_bounds(thresholds.band_curvature, len(ladder)))
    total = math.fsum(masses)
    distribution = GradeDistribution(
        {grade: mass / total for grade, mass in zip(ladder, masses, strict=True)}
    )
    TAG_SCALE.validate(distribution)

    return GradePrediction(
        grade_probability=distribution,
        # ADR 0011 decision 1's two bounds: never more certain than the evidence
        # read, and never more certain than an uncalibrated mapping is entitled
        # to be. It is not the distribution's spread — a distribution can be
        # narrow and wrong, and `expected_value` takes this as
        # `distribution_confidence`, so conflating them reaches the economics.
        model_confidence=Confidence(min(condition.confidence.value, thresholds.confidence_ceiling)),
        model_version=GRADING_TAG_VERSION,
    )


def _band_bounds(curvature: float, grades: int) -> tuple[float, ...]:
    """The score range each grade owns, as ``grades + 1`` ascending bounds.

    ``bounds[i]`` and ``bounds[i + 1]`` are the score interval of
    ``TAG_SCALE.ordered[i]``. The interior edges are
    ``TAG_SCORE_MAXIMUM * (1 - (1 - k/grades) ** curvature)``, which is strictly
    increasing for any positive curvature and, above 1.0, tightens toward the
    top of the ladder.

    The two outer bounds are ``0`` and :data:`TAG_SCORE_MAXIMUM` rather than
    infinities, and that **truncation is load-bearing**: a sub-score is clamped
    to that range, so the distribution over scores lives there too. An unbounded
    top tail hands a card nobody scanned about a fifth of its mass on grade 10 —
    the fabricated optimism the blend exists to prevent, re-entering through the
    integral instead of through the centre.
    """
    interior = tuple(
        TAG_SCORE_MAXIMUM * (1.0 - (1.0 - step / grades) ** curvature) for step in range(1, grades)
    )
    return (0.0, *interior, TAG_SCORE_MAXIMUM)


def _band_masses(score: float, sigma: float, bounds: Sequence[float]) -> list[float]:
    """How much of a normal centred on `score` falls in each band.

    Unnormalised: the caller divides by the total, which is also what renormalises
    the truncation. ``max(0.0, …)`` is not defensive — two nearly equal cumulative
    values differ by a negative epsilon often enough, and `GradeDistribution`
    refuses a probability outside ``[0, 1]``.
    """
    cumulative = [_standard_normal_cdf((bound - score) / sigma) for bound in bounds]
    return [max(0.0, upper - lower) for lower, upper in itertools.pairwise(cumulative)]


def _standard_normal_cdf(z: float) -> float:
    """Φ, from `math.erf`. No numpy and no scipy for one closed form."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _deduction(severity: DefectSeverity | None, thresholds: TAGGradingThresholds) -> float:
    """What one finding of this severity takes off its category's sub-score.

    ``None`` is the domain's spelling for a finding that claims no defect —
    ``clean``, and ``unknown`` where the scan saw a region it could not read.
    Neither deducts; the second reduces coverage instead.
    """
    if severity is DefectSeverity.MINOR:
        return thresholds.minor_deduction
    if severity is DefectSeverity.MODERATE:
        return thresholds.moderate_deduction
    if severity is DefectSeverity.SEVERE:
        return thresholds.severe_deduction
    return 0.0


def _scored(deduction: float) -> float:
    """A sub-score is what survives its deductions, clamped at zero.

    The clamp is the whole of TAG's saturation rule, and it is why there is no
    per-category saturation constant: a card that far gone has no points left,
    and a scale that scored below zero would not be a scale.
    """
    return max(0.0, TAG_SCORE_MAXIMUM - deduction)


def _centering_category(
    centering: Uncertain[Centering], thresholds: TAGGradingThresholds
) -> _Category:
    """Centering's sub-score and coverage.

    The four ratios are averaged **unweighted**: a scanner measures both faces
    with the same instrument and does not know which one it is looking at. PSA's
    `front_centering_share` has no counterpart here, and its absence is the
    point rather than an omission.
    """
    if isinstance(centering, InsufficientInformation):
        return _Category(subscore=TAG_SCORE_MAXIMUM, coverage=0.0)

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
        return _Category(subscore=TAG_SCORE_MAXIMUM, coverage=0.0)

    mean_offset = math.fsum(offsets) / len(offsets)
    return _Category(
        subscore=_scored(thresholds.centering_full_deduction * mean_offset),
        coverage=len(offsets) / len(ratios),
    )


def _region_category[R](
    sides: Mapping[ImageSide, Uncertain[Mapping[R, RegionFinding]]],
    *,
    regions_per_side: int,
    thresholds: TAGGradingThresholds,
) -> _Category:
    """Corners' or edges' sub-score and coverage — one function, because the two
    categories differ only in their region vocabulary.

    A region labelled ``unknown`` is one the scan could not read (`ml/corners`:
    *"a corner it cannot judge is `unknown`, never a guessed `clean`"*), so it
    deducts nothing and lowers coverage instead — which is
    :func:`~tcg_domain.condition.region_coverage`'s rule, counted in the domain
    since #225 so that the three predictors count once and score three times.
    A refused side is all of its regions at once.
    """
    deduction = 0.0
    for side in V1_SIDES:
        findings = sides[side]
        if isinstance(findings, InsufficientInformation):
            continue
        for finding in findings.values():
            deduction += _deduction(finding.severity, thresholds)
    return _Category(
        subscore=_scored(deduction),
        coverage=region_coverage(sides, regions_per_side=regions_per_side),
    )


def _surface_category(
    sides: Mapping[ImageSide, Uncertain[SurfaceAssessment]], thresholds: TAGGradingThresholds
) -> _Category:
    """Surface's sub-score and coverage.

    ``not_assessed`` is the coverage signal and it is load-bearing: ADR 0010's
    fine classes are refused class-level by the v0.1.0 baseline, so a surface
    side that answered still leaves most of §16's vocabulary unread, and
    :func:`~tcg_domain.condition.surface_coverage` says so instead of taking an
    empty `findings` tuple for a clean card.
    """
    deduction = 0.0
    for side in V1_SIDES:
        face = sides[side]
        if isinstance(face, InsufficientInformation):
            continue
        deduction += math.fsum(_deduction(defect.severity, thresholds) for defect in face.findings)
    return _Category(subscore=_scored(deduction), coverage=surface_coverage(sides))
