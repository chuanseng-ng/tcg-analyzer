"""The per-company predictors are not one mapping wearing three version strings.

CLAUDE.md's master architectural rule, and spec §2.2:

    Condition is separate from grading-company prediction. The pipeline is
    `images → one neutral condition representation → {PSA model, TAG model, BGS
    model}`. Each company gets its own model. **Never build a single universal
    `condition_score → grade` mapping.**

That rule is easy to keep by accident and easy to break by accident, because
`PSA_SCALE.ordered` and `TAG_SCALE.ordered` are index-for-index identical —
eighteen points, half steps from 1 to 9, then 10, no 9.5 on either. A TAG
predictor written as PSA's rule with different weights would satisfy every test
in `ml/grading/tag` and still be the forbidden mapping, shipped twice. #224's
acceptance criterion is therefore that *whether* the two differ is **a stated,
tested fact**, and this is where it is tested.

BGS joined at #225 and is the easier half of the same claim in one respect and
the harder half in another. Easier: its ladder really is different — nineteen
points, with the 9.5 the other two refuse — so nothing it answers could be
mistaken for either sibling's output. Harder: a different ladder proves nothing
about the *rule*, and the rule is what §2.2 is about. So the assertions below
compare the three mappings' **shapes** on the same inputs, in the ladder's own
units, and BGS's claim is that it takes the worst of four subgrades where the
other two add or average.

Why this lives at the repository root rather than in either package
-------------------------------------------------------------------
It is the only test that imports two predictors, and neither package may import
the other: ADR 0011 forbids a shared `ml/grading/common` on the grounds that a
shared grading package is the universal mapping arriving by the back door, and a
sibling import is that package with one member. Putting the comparison here costs
`ml/grading/tag` no dependency on `tcg-ml-grading-psa` and keeps the claim where
it belongs — beside `test_repository_structure.py`, as a statement about the
repository rather than about a package.

Like every other root test it needs no database, no object storage and no image.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from decimal import Decimal

import pytest
from tcg_domain.analysis import V1_SIDES
from tcg_domain.annotation import (
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
)
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    RegionFinding,
    SurfaceAssessment,
)
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence
from tcg_domain.distribution import GradeDistribution
from tcg_domain.grade import Grade
from tcg_grading_companies.companies import BGS_SCALE, PSA_SCALE, TAG_SCALE
from tcg_grading_companies.errors import UnsupportedGrade
from tcg_grading_companies.port import GradePrediction
from tcg_grading_companies.scale import GradeScale
from tcg_ml_grading_bgs import predict as predict_bgs
from tcg_ml_grading_psa import predict as predict_psa
from tcg_ml_grading_tag import DEFAULT_TAG_GRADING_THRESHOLDS
from tcg_ml_grading_tag import predict as predict_tag

#: Every predictor's signature, as far as this file needs it. Deliberately not
#: `GradingCompanyAdapter.predict_grade`: nothing here goes through an adapter,
#: because this claim is about the mappings — #226's seam has its own root
#: test, `test_adapters_carry_the_predictors.py`.
type _Predictor = Callable[[ConditionAssessment], GradePrediction]

#: The three mappings and the ladder each answers on. A fourth company would be
#: one more entry and no other change here.
COMPANIES: tuple[tuple[str, _Predictor, GradeScale], ...] = (
    ("psa", predict_psa, PSA_SCALE),
    ("tag", predict_tag, TAG_SCALE),
    ("bgs", predict_bgs, BGS_SCALE),
)

# ----------------------------------------------------------------
# Builders — a fourth copy, for the reason each of the other three states
# ----------------------------------------------------------------


def _sure() -> Confidence:
    return Confidence(0.9)


def _corners(
    label: CornerLabel, severity: DefectSeverity | None = None
) -> dict[CornerRegion, RegionFinding]:
    return {
        region: RegionFinding(label=label, confidence=_sure(), severity=severity)
        for region in CornerRegion
    }


def _edges(
    label: EdgeLabel, severity: DefectSeverity | None = None
) -> dict[EdgeRegion, RegionFinding]:
    return {
        region: RegionFinding(label=label, confidence=_sure(), severity=severity)
        for region in EdgeRegion
    }


def _assessment(**overrides: object) -> ConditionAssessment:
    """A fully measured, undamaged card."""
    values: dict[str, object] = {
        "centering": Centering(
            front_horizontal=0.5,
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=_sure(),
        ),
        "corners": dict.fromkeys(V1_SIDES, _corners(CornerLabel.CLEAN)),
        "edges": dict.fromkeys(V1_SIDES, _edges(EdgeLabel.CLEAN)),
        "surface": dict.fromkeys(V1_SIDES, SurfaceAssessment(findings=())),
        "manufacturing_defects": (),
        "eye_appeal": INSUFFICIENT_INFORMATION,
        "confidence": Confidence(0.9),
    }
    values.update(overrides)
    return ConditionAssessment(**values)  # type: ignore[arg-type]


def _damaged_corners_only() -> ConditionAssessment:
    return _assessment(
        corners=dict.fromkeys(V1_SIDES, _corners(CornerLabel.CHIPPING, DefectSeverity.SEVERE))
    )


def _damaged_edges_only() -> ConditionAssessment:
    return _assessment(
        edges=dict.fromkeys(V1_SIDES, _edges(EdgeLabel.WHITENING, DefectSeverity.SEVERE))
    )


def _off_centre(fraction: float) -> ConditionAssessment:
    """A card off-centre by `fraction` of the tolerance both predictors declare.

    Both read a ratio as `near / (near + far)` with 0.5 perfect, and both declare
    the same 0.40 offset as full penalty, so one ratio drives both sweeps and the
    comparison is about the mapping rather than about the input.
    """
    offset = DEFAULT_TAG_GRADING_THRESHOLDS.centering_tolerance_offset * fraction
    ratio = 0.5 + offset / 2.0
    return _assessment(
        centering=Centering(
            front_horizontal=ratio,
            front_vertical=ratio,
            back_horizontal=ratio,
            back_vertical=ratio,
            confidence=_sure(),
        )
    )


def _two_damaged_categories() -> ConditionAssessment:
    """The same wrecked corners as `_damaged_corners_only`, and the edges wrecked
    as well. A rule that *adds* must answer worse than for one alone."""
    return _assessment(
        corners=dict.fromkeys(V1_SIDES, _corners(CornerLabel.CHIPPING, DefectSeverity.MODERATE)),
        edges=dict.fromkeys(V1_SIDES, _edges(EdgeLabel.WHITENING, DefectSeverity.MODERATE)),
    )


def _one_damaged_category() -> ConditionAssessment:
    return _assessment(
        corners=dict.fromkeys(V1_SIDES, _corners(CornerLabel.CHIPPING, DefectSeverity.MODERATE))
    )


def _mean_position(distribution: GradeDistribution, scale: GradeScale) -> float:
    """Where the mass sits on the company's own ladder, as a mean index.

    An index, never a grade value: ±1 is a step on the company's ladder (ADR 0011
    decision 2), and arithmetic on the value would compare 8.5-to-9 against
    9-to-10 as if they were the same distance.
    """
    return math.fsum(
        position * distribution.probability_of(grade)
        for position, grade in enumerate(scale.ordered)
    )


def _consecutive_drops(
    fractions: tuple[float, ...], predict: _Predictor, scale: GradeScale
) -> list[float]:
    positions = [
        _mean_position(predict(_off_centre(fraction)).grade_probability, scale)
        for fraction in fractions
    ]
    return [earlier - later for earlier, later in itertools.pairwise(positions)]


# ----------------------------------------------------------------
# The two mappings
# ----------------------------------------------------------------


def test_each_predictor_answers_on_its_own_scale() -> None:
    """The premise the rest of this file rests on. PSA's and TAG's ladders are the
    same shape, so nothing said about *those two* can be explained away as the
    companies grading on different scales. BGS's is genuinely different, which is
    why its claim is made in ladder positions rather than in grade values."""
    assert PSA_SCALE.ordered == TAG_SCALE.ordered
    assert len(BGS_SCALE.ordered) == len(PSA_SCALE.ordered) + 1

    for _, predict, scale in COMPANIES:
        scale.validate(predict(_assessment()).grade_probability)


def test_one_assessment_does_not_get_one_answer() -> None:
    """The blunt form of the rule. Identical ladders, identical input, and PSA and
    TAG must still say different things — otherwise there is one mapping here and
    it has been given two names.

    BGS is left out of this one on purpose: its ladder differs, so a different
    mapping is guaranteed for a reason that says nothing about the rule. Its
    claim is `test_bgs_takes_the_worst_category_where_the_others_accumulate`.
    """
    for condition in (_assessment(), _damaged_corners_only(), _off_centre(1.0)):
        psa = predict_psa(condition).grade_probability
        tag = predict_tag(condition).grade_probability

        assert psa.as_mapping() != tag.as_mapping()


def test_psa_ranks_the_axes_and_tag_does_not() -> None:
    """The first place the two rules disagree in kind rather than in degree.

    PSA weighs a fully damaged axis in ladder steps and ranks them — corners cost
    5, edges 3 — because that is how an eye grades. TAG scores four categories
    with one instrument on a common 1000-point scale and weighs them equally,
    because this project holds no measurement that would justify preferring one.

    So swapping *which* axis is wrecked moves PSA's answer and leaves TAG's
    exactly where it was. No choice of weights gives PSA that property, and none
    takes it from TAG.
    """
    corners = _damaged_corners_only()
    edges = _damaged_edges_only()

    psa_corners = predict_psa(corners).grade_probability
    psa_edges = predict_psa(edges).grade_probability
    tag_corners = predict_tag(corners).grade_probability
    tag_edges = predict_tag(edges).grade_probability

    assert psa_corners.most_likely_grade != psa_edges.most_likely_grade
    assert tag_corners.most_likely_grade == tag_edges.most_likely_grade
    assert tag_corners.as_mapping() == tag_edges.as_mapping()


def test_psa_walks_the_ladder_in_even_steps_and_tag_does_not() -> None:
    """The second, and the one that cannot be reached by retuning either package.

    PSA subtracts weighted damage from a position on the ladder, so equal
    increments of damage cost roughly equal numbers of positions — the mapping is
    linear in the ladder's own units, and what curvature it has comes from the
    ladder ending at 10 rather than from the rule.

    TAG subtracts points from a 1000-point score and then places that score in a
    band table whose bands widen as they descend, so the *same* increment of
    damage costs fewer positions the further down the card already is. The drops
    shrink monotonically. PSA's do not — they go the other way.
    """
    fractions = (0.4, 0.6, 0.8, 1.0)
    tag_drops = _consecutive_drops(fractions, predict_tag, TAG_SCALE)
    psa_drops = _consecutive_drops(fractions, predict_psa, PSA_SCALE)

    assert all(drop > 0.0 for drop in tag_drops + psa_drops), "both must fall as damage grows"

    assert tag_drops == sorted(tag_drops, reverse=True)
    assert tag_drops[0] > tag_drops[-1]

    assert psa_drops[0] < psa_drops[-1]


def test_the_three_versions_are_named_apart() -> None:
    """A shared mapping would still need three version strings, so this proves
    nothing on its own — but a *collision* would make the reproducibility record
    unable to say which model ran, which is the failure the naming exists to
    prevent."""
    versions = [predict(_assessment()).model_version for _, predict, _ in COMPANIES]

    assert len(set(versions)) == len(COMPANIES)
    for (slug, _, _), version in zip(COMPANIES, versions, strict=True):
        assert version.startswith(f"grading-{slug}-")


@pytest.mark.parametrize(("predict", "scale"), [(p, s) for _, p, s in COMPANIES])
def test_no_predictor_refuses_an_assessment_that_measured_nothing(
    predict: _Predictor, scale: GradeScale
) -> None:
    """ADR 0011 decision 1, held identically by all three — the one place the
    three packages must agree, because it is the ADR's rule rather than a
    company's."""
    nothing = _assessment(
        centering=INSUFFICIENT_INFORMATION,
        corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        edges=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        surface=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        manufacturing_defects=INSUFFICIENT_INFORMATION,
        confidence=Confidence(0.0),
    )

    prediction = predict(nothing)

    assert prediction.grade_probability.most_likely_grade < scale.ordered[-1]


# ----------------------------------------------------------------
# The third mapping
# ----------------------------------------------------------------


def test_bgs_is_the_only_ladder_with_a_9_5() -> None:
    """Spec §24's own closing sentence — *"BGS must support half grades"* — as an
    assertion about all three predictors at once.

    9.5 is the grade the three companies actually disagree about: PSA says in its
    own words that it deliberately has none, TAG's published table has no 9.5
    row, and BGS steps by a half point all the way. A predictor that had quietly
    reused a sibling's ladder would pass every test inside `ml/grading/bgs` and
    fail here, which is the failure this file exists to catch.
    """
    nine_and_a_half = Grade(Decimal("9.5"))
    answers = {slug: predict(_assessment()).grade_probability for slug, predict, _ in COMPANIES}

    assert nine_and_a_half in set(answers["bgs"])
    assert nine_and_a_half not in set(answers["psa"])
    assert nine_and_a_half not in set(answers["tag"])

    # And it is not merely present: the scales themselves refuse each other's
    # output, which is what makes the ladder a per-company fact rather than a
    # rendering detail.
    for scale in (PSA_SCALE, TAG_SCALE):
        with pytest.raises(UnsupportedGrade):
            scale.validate(answers["bgs"])


def test_bgs_takes_the_worst_category_where_the_others_accumulate() -> None:
    """The rule claim, and the one a different ladder does not make for free.

    PSA sums weighted damage across the axes and TAG averages four sub-scores, so
    wrecking a *second* category costs both of them again. BGS takes the worst of
    four printed subgrades — `companies.py` records that Beckett prints them —
    so once the corners are down, taking the edges down as far tells the rule
    nothing it did not already know.

    Measured in ladder positions, in each company's own units, so the comparison
    survives BGS having one more of them.
    """
    drops = {}
    for slug, predict, scale in COMPANIES:
        one = _mean_position(predict(_one_damaged_category()).grade_probability, scale)
        two = _mean_position(predict(_two_damaged_categories()).grade_probability, scale)
        drops[slug] = one - two

    assert drops["psa"] > 1.0
    assert drops["tag"] > 1.0
    assert drops["bgs"] < 0.5


def test_bgs_moves_in_half_grade_steps_where_the_others_slide() -> None:
    """The placement claim. A BGS subgrade is quantised onto the ladder before it
    is compared, because Beckett prints 9.5 and never 9.37 — so as damage grows
    continuously the answer **plateaus**. PSA walks a continuous centre and TAG
    integrates a continuous score over its bands; both slide at every increment.

    This is the second property no retuning of either sibling reaches, and the
    plateaus are what fail if the quantisation is ever removed as a rounding
    nicety.
    """
    fractions = (0.30, 0.32, 0.34, 0.36, 0.38, 0.40)
    positions = {
        slug: [
            _mean_position(predict(_off_centre(fraction)).grade_probability, scale)
            for fraction in fractions
        ]
        for slug, predict, scale in COMPANIES
    }

    for slug, walk in positions.items():
        assert walk == sorted(walk, reverse=True), f"{slug} must not improve as damage grows"

    assert len(set(positions["bgs"])) < len(fractions)
    assert len(set(positions["psa"])) == len(fractions)
    assert len(set(positions["tag"])) == len(fractions)
