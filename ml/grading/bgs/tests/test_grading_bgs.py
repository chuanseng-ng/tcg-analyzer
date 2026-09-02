"""BGS grade prediction — spec §24, issue #225.

The builders below are a third hand-written copy of `ml/grading/psa`'s and
`ml/grading/tag`'s, and deliberately so: the three packages share no test
package, and importing across would be a dependency nothing declares (#221's
rule for `REFUSED_ASSESSMENT`, which stayed out of `packages/domain`'s factory
for the same reason).

**Nothing here imports `tcg_ml_grading_psa` or `tcg_ml_grading_tag`.** The
comparison between the three mappings lives at the repository root, in
`tests/test_grade_predictors_differ.py`, which is what keeps this package free of
a sibling dependency in metadata as well as in code.

The assertion this file exists for is
`test_a_distribution_naming_9_5_is_bgs_only`: 9.5 is the grade the three
companies actually disagree about, and it must be legal here and illegal at PSA.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import textwrap
from decimal import Decimal

import pytest
from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.annotation import (
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    Defect,
    RegionFinding,
    Representation,
    SurfaceAssessment,
)
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence, InsufficientInformation
from tcg_domain.distribution import SUM_TOLERANCE, GradeDistribution
from tcg_domain.grade import Grade
from tcg_grading_companies.companies import BGS_SCALE, PSA_SCALE
from tcg_grading_companies.errors import UnsupportedGrade
from tcg_grading_companies.port import GradePrediction
from tcg_ml_grading_bgs import (
    DEFAULT_BGS_GRADING_THRESHOLDS,
    GRADING_BGS_VERSION,
    BGSGradingThresholds,
    predict,
)

#: The grade this whole package exists to reach. BGS issues it; PSA and TAG do
#: not, and `companies.py` records both companies' own words for why.
NINE_AND_A_HALF = Grade(Decimal("9.5"))

#: §59's grammar, with the `heuristic` infix ADR 0011 decision 6 requires of a
#: baseline. `grading-bgs-v0.2.0` is reserved for the trained bundle.
VERSION_GRAMMAR = re.compile(r"^grading-bgs-heuristic-v\d+\.\d+\.\d+$")


# ----------------------------------------------------------------
# Builders
# ----------------------------------------------------------------


def sure() -> Confidence:
    return Confidence(0.9)


def corners(
    label: CornerLabel, severity: DefectSeverity | None = None
) -> dict[CornerRegion, RegionFinding]:
    return {
        region: RegionFinding(label=label, confidence=sure(), severity=severity)
        for region in CornerRegion
    }


def edges(
    label: EdgeLabel, severity: DefectSeverity | None = None
) -> dict[EdgeRegion, RegionFinding]:
    return {
        region: RegionFinding(label=label, confidence=sure(), severity=severity)
        for region in EdgeRegion
    }


def assessment(**overrides: object) -> ConditionAssessment:
    """A fully measured, undamaged card."""
    values: dict[str, object] = {
        "centering": Centering(
            front_horizontal=0.5,
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure(),
        ),
        "corners": dict.fromkeys(V1_SIDES, corners(CornerLabel.CLEAN)),
        "edges": dict.fromkeys(V1_SIDES, edges(EdgeLabel.CLEAN)),
        "surface": dict.fromkeys(V1_SIDES, SurfaceAssessment(findings=())),
        "manufacturing_defects": (),
        "eye_appeal": INSUFFICIENT_INFORMATION,
        "confidence": Confidence(0.9),
    }
    values.update(overrides)
    return ConditionAssessment(**values)  # type: ignore[arg-type]


def one_flawed_corner(severity: DefectSeverity) -> ConditionAssessment:
    """A single flawed corner on the front, everything else read and clean."""
    flawed = corners(CornerLabel.CLEAN)
    flawed[CornerRegion.TOP_LEFT] = RegionFinding(
        label=CornerLabel.WHITENING, confidence=sure(), severity=severity
    )
    return assessment(corners={ImageSide.FRONT: flawed, ImageSide.BACK: corners(CornerLabel.CLEAN)})


def measured_nothing() -> ConditionAssessment:
    return assessment(
        centering=INSUFFICIENT_INFORMATION,
        corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        edges=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        surface=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        manufacturing_defects=INSUFFICIENT_INFORMATION,
        confidence=Confidence(0.0),
    )


def answered(prediction: object) -> GradePrediction:
    """Narrow the port's `Uncertain` for the checker.

    v0.1.0 never refuses, so this is an assertion rather than a branch — and one
    that fails loudly if a coverage gate is ever added.
    """
    assert isinstance(prediction, GradePrediction), prediction
    return prediction


def mean_position(distribution: GradeDistribution) -> float:
    """Where the mass sits on BGS's ladder, as a mean **index**.

    An index, never a grade value: ±1 is a step on the company's ladder (ADR
    0011 decision 2).
    """
    return math.fsum(
        position * distribution.probability_of(grade)
        for position, grade in enumerate(BGS_SCALE.ordered)
    )


# ----------------------------------------------------------------
# Nineteen grades, and the one the companies disagree about
# ----------------------------------------------------------------


def test_a_distribution_naming_9_5_is_bgs_only() -> None:
    """**The issue's whole point.** BGS issues nineteen grades where PSA and TAG
    issue eighteen, and the extra one is 9.5. A distribution carrying it is valid
    for this company and refused for the other two, which is what makes the grade
    scale per company rather than shared."""
    distribution = answered(predict(assessment())).grade_probability

    assert NINE_AND_A_HALF in set(distribution)
    BGS_SCALE.validate(distribution)

    with pytest.raises(UnsupportedGrade, match=r"9\.5"):
        PSA_SCALE.validate(distribution)


def test_the_mapping_actually_reaches_9_5() -> None:
    """A ladder carrying 9.5 as a key nobody ever lands on would make the whole
    seam dead code — `expected_value` prices per `(company, grade)` and
    `GET /cards/{id}/market` returns 55 pairs rather than 54 precisely because of
    this grade. A slightly flawed card must actually answer it."""
    prediction = answered(predict(one_flawed_corner(DefectSeverity.MINOR)))

    assert prediction.grade_probability.most_likely_grade == NINE_AND_A_HALF


def test_the_distribution_covers_the_whole_ladder_and_never_a_bucket() -> None:
    """ADR 0011 decision 4: the full per-company ladder, no bucket. Nineteen
    keys, not PSA's eighteen — a predictor that had silently reused the sibling
    ladder fails here."""
    distribution = answered(predict(assessment())).grade_probability

    assert set(distribution) == set(BGS_SCALE.grades)
    assert len(BGS_SCALE.ordered) == 19
    assert not any(grade.is_bucket for grade in distribution)
    assert math.fsum(distribution.as_mapping().values()) == pytest.approx(1.0, abs=SUM_TOLERANCE)


def test_no_subgrade_and_no_designation_is_emitted() -> None:
    """BGS prints four subgrades and one Black Label. Neither is a value on the
    scale: §24's output is a distribution over the overall grade, and Black Label
    is a label *on* grade 10 — the predictor answers 10."""
    distribution = answered(predict(assessment())).grade_probability

    assert all(isinstance(grade, Grade) for grade in distribution)
    assert Grade(Decimal("10")) in set(distribution)


# ----------------------------------------------------------------
# The rule: the worst subgrade decides
# ----------------------------------------------------------------


def test_wrecking_a_second_category_does_not_worsen_the_card() -> None:
    """BGS's rule, and the one no choice of weights reaches. The overall grade is
    the worst of four printed subgrades, so once the corners are down, taking the
    edges down as far barely moves the answer — where a weighted sum (PSA) or a
    weighted mean (TAG) would fall again by the same amount.

    "Barely" rather than "not at all" because the subgrades are uncertain, and
    the minimum of two uncertain readings sits a little below one of them. That
    residue is the rule's, not a sum leaking in: it is a small fraction of a
    ladder step, where a sum would cost several."""
    flawed = corners(CornerLabel.CLEAN)
    for region in list(CornerRegion)[:2]:
        flawed[region] = RegionFinding(
            label=CornerLabel.WHITENING, confidence=sure(), severity=DefectSeverity.MODERATE
        )
    flawed_edges = edges(EdgeLabel.CLEAN)
    for region in list(EdgeRegion)[:2]:
        flawed_edges[region] = RegionFinding(
            label=EdgeLabel.WHITENING, confidence=sure(), severity=DefectSeverity.MODERATE
        )

    one = assessment(corners={ImageSide.FRONT: flawed, ImageSide.BACK: corners(CornerLabel.CLEAN)})
    two = assessment(
        corners={ImageSide.FRONT: flawed, ImageSide.BACK: corners(CornerLabel.CLEAN)},
        edges={ImageSide.FRONT: flawed_edges, ImageSide.BACK: edges(EdgeLabel.CLEAN)},
    )

    one_position = mean_position(answered(predict(one)).grade_probability)
    two_position = mean_position(answered(predict(two)).grade_probability)

    assert (
        answered(predict(one)).grade_probability.most_likely_grade
        == answered(predict(two)).grade_probability.most_likely_grade
    )
    assert one_position - two_position < 0.5


def test_damage_inside_one_category_does_accumulate() -> None:
    """The other half of the asymmetry, and it is BGS's rather than an
    inconsistency: Beckett prints **one** number for all four corners, so a
    second chipped corner does make that subgrade worse. It is only *across*
    categories that the rule stops adding and starts taking the worst."""
    one = mean_position(
        answered(predict(one_flawed_corner(DefectSeverity.MODERATE))).grade_probability
    )

    both = corners(CornerLabel.CLEAN)
    for region in list(CornerRegion)[:2]:
        both[region] = RegionFinding(
            label=CornerLabel.WHITENING, confidence=sure(), severity=DefectSeverity.MODERATE
        )
    two = mean_position(
        answered(
            predict(
                assessment(
                    corners={ImageSide.FRONT: both, ImageSide.BACK: corners(CornerLabel.CLEAN)}
                )
            )
        ).grade_probability
    )

    assert two < one - 1.0


def test_a_worse_card_answers_a_worse_grade() -> None:
    """The severity chain reaches the answer in order."""
    positions = [
        mean_position(answered(predict(one_flawed_corner(severity))).grade_probability)
        for severity in (DefectSeverity.MINOR, DefectSeverity.MODERATE, DefectSeverity.SEVERE)
    ]

    assert positions == sorted(positions, reverse=True)
    assert positions[0] > positions[-1]


def test_a_pristine_10_needs_all_four_subgrades_at_10() -> None:
    """`companies.py`'s own definition of Black Label — *"a BGS 10 whose four
    subgrades are each 10"* — as arithmetic. Under the minimum rule
    ``P(BGS 10) = Π P(subgrade = 10)`` over the four, which is why a BGS 10 is
    harder to reach here than a PSA 10 is in `ml/grading/psa`, and why even a
    flawlessly read card answers 9.5.

    The number is pinned so that tightening `base_sigma` — the one change that
    would quietly buy this predictor unearned certainty — cannot pass silently.
    """
    distribution = answered(predict(assessment())).grade_probability

    assert distribution.most_likely_grade == NINE_AND_A_HALF
    assert distribution.probability_of(Grade(Decimal("10"))) == pytest.approx(0.405, abs=0.005)


def test_the_answer_moves_in_half_grade_steps() -> None:
    """Beckett prints 9.5 and never 9.37, so a subgrade lands on the ladder
    before it is compared. The consequence is visible: as damage grows
    continuously the answer **plateaus**, where both siblings slide.

    This is what "half-step resolution" buys, and the plateaus are what fail if
    the quantisation is ever removed as a rounding nicety."""
    positions = []
    for fraction in (0.30, 0.32, 0.34, 0.36, 0.38, 0.40):
        offset = DEFAULT_BGS_GRADING_THRESHOLDS.centering_tolerance_offset * fraction
        ratio = 0.5 + offset / 2.0
        positions.append(
            mean_position(
                answered(
                    predict(
                        assessment(
                            centering=Centering(
                                front_horizontal=ratio,
                                front_vertical=ratio,
                                back_horizontal=ratio,
                                back_vertical=ratio,
                                confidence=sure(),
                            )
                        )
                    )
                ).grade_probability
            )
        )

    assert positions == sorted(positions, reverse=True)
    assert len(set(positions)) < len(positions), "a quantised subgrade must plateau"


# ----------------------------------------------------------------
# Ignorance
# ----------------------------------------------------------------


def test_measuring_nothing_does_not_answer_pristine() -> None:
    """The guard on the per-category blend, and it is not optional: an unread
    category loses nothing, so an unblended subgrade sits at 10 and four of them
    answer *"probably a Pristine 10"* — #91's "not measured is never 0%"
    committed in the optimistic direction. Removing the blend is silent: every
    test about damage above still passes."""
    distribution = answered(predict(measured_nothing())).grade_probability

    assert distribution.most_likely_grade < NINE_AND_A_HALF
    assert distribution.probability_of(Grade(Decimal("10"))) < 0.01
    assert mean_position(distribution) < len(BGS_SCALE.ordered) / 2 + 1.0


def test_a_thinner_assessment_widens_rather_than_refusing() -> None:
    """ADR 0011 decision 1: no coverage gate and no confidence gate. Less
    evidence is a wider distribution, never a refusal."""
    full = answered(predict(assessment())).grade_probability
    thin = answered(predict(measured_nothing())).grade_probability

    assert max(thin.as_mapping().values()) < max(full.as_mapping().values())


def test_an_unread_region_widens_rather_than_scoring() -> None:
    """`ml/corners` answers `unknown` for a corner it cannot judge, never a
    guessed `clean`. That refusal must lower coverage, not damage."""
    unknown = corners(CornerLabel.CLEAN)
    unknown[CornerRegion.TOP_LEFT] = RegionFinding(label=CornerLabel.UNKNOWN, confidence=sure())
    partial = answered(
        predict(
            assessment(
                corners={ImageSide.FRONT: unknown, ImageSide.BACK: corners(CornerLabel.CLEAN)}
            )
        )
    ).grade_probability
    full = answered(predict(assessment())).grade_probability

    assert max(partial.as_mapping().values()) < max(full.as_mapping().values())


def test_an_answered_surface_side_is_not_read_as_a_clean_one() -> None:
    """ADR 0010's fine classes are refused class-level by `ml/surface`'s v0.1.0
    baseline, so an empty `findings` tuple is a partly examined surface rather
    than a flawless one — `surface_coverage` says so, and this asserts the
    predictor listens."""
    refused = SurfaceAssessment(
        findings=(),
        not_assessed=dict.fromkeys(
            (SurfaceLabel.SCRATCH, SurfaceLabel.PRINT_LINE), INSUFFICIENT_INFORMATION
        ),
    )
    partial = answered(
        predict(assessment(surface=dict.fromkeys(V1_SIDES, refused)))
    ).grade_probability
    full = answered(predict(assessment())).grade_probability

    assert max(partial.as_mapping().values()) < max(full.as_mapping().values())


def test_a_surface_finding_penalises_its_own_subgrade() -> None:
    stained = SurfaceAssessment(
        findings=(
            Defect(
                type=SurfaceLabel.STAIN,
                confidence=sure(),
                severity=DefectSeverity.MODERATE,
                side=ImageSide.FRONT,
                representation=Representation.NORMALIZED,
            ),
        )
    )
    marked = answered(
        predict(
            assessment(
                surface={
                    ImageSide.FRONT: stained,
                    ImageSide.BACK: SurfaceAssessment(findings=()),
                }
            )
        )
    ).grade_probability

    assert mean_position(marked) < mean_position(answered(predict(assessment())).grade_probability)


def test_it_never_refuses_however_little_it_was_given() -> None:
    """ADR 0011 decision 1 in its blunt form. The only refusal in this step is a
    refusal on the way in, which the caller propagates without ever building an
    assessment (#227) — so this arm is unreachable in v0.1.0 and no reason
    vocabulary was coined for it."""
    for condition in (assessment(), measured_nothing(), one_flawed_corner(DefectSeverity.SEVERE)):
        assert not isinstance(predict(condition), InsufficientInformation)


# ----------------------------------------------------------------
# The prediction's own metadata
# ----------------------------------------------------------------


def test_model_confidence_never_exceeds_either_bound() -> None:
    """ADR 0011 decision 1's two bounds. It is **not** the distribution's spread:
    `expected_value` takes it as `distribution_confidence`, so conflating the two
    reaches the economics."""
    ceiling = DEFAULT_BGS_GRADING_THRESHOLDS.confidence_ceiling

    confident = answered(predict(assessment(confidence=Confidence(1.0))))
    assert confident.model_confidence.value == pytest.approx(ceiling)

    unsure = answered(predict(assessment(confidence=Confidence(0.1))))
    assert unsure.model_confidence.value == pytest.approx(0.1)


def test_the_version_is_a_code_constant_in_section_59s_grammar() -> None:
    """ADR 0011 decision 6: a baseline's version is a code constant, never a
    `model_bundles` row. The `heuristic` infix keeps it from colliding with the
    trained `grading-bgs-v0.2.0` in `analyses.model_bundle_version`."""
    assert VERSION_GRAMMAR.match(GRADING_BGS_VERSION)
    assert answered(predict(assessment())).model_version == GRADING_BGS_VERSION


def test_the_same_assessment_answers_the_same_distribution() -> None:
    """Nothing is sampled and nothing is fitted."""
    first = answered(predict(assessment())).grade_probability
    second = answered(predict(assessment())).grade_probability

    assert first.as_mapping() == second.as_mapping()


# ----------------------------------------------------------------
# The declared constants
# ----------------------------------------------------------------


def test_the_thresholds_record_carries_its_own_prefix() -> None:
    """A stored prediction explains itself, and cannot collide with the four
    analyzers' records or with `grading_psa_`'s and `grading_tag_`'s in the same
    `condition_details` document."""
    record = DEFAULT_BGS_GRADING_THRESHOLDS.as_record()

    assert all(name.startswith("grading_bgs_") for name in record)
    assert all(isinstance(value, float) for value in record.values())
    assert json.loads(json.dumps(record)) == record
    assert "grading_bgs_unmeasured_subgrade" in record


def test_there_are_no_category_weights() -> None:
    """A minimum has none. Asking which category's subgrade matters more is
    asking for a weighted mean, which is TAG's aggregation retuned rather than a
    third rule — so the constant that would express it must not exist."""
    assert not [name for name in DEFAULT_BGS_GRADING_THRESHOLDS.as_record() if "weight" in name]


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_sigma": 0.0},
        {"centering_full_penalty": -1.0},
        {"unmeasured_sigma": -0.5},
        {"confidence_ceiling": 0.0},
        {"centering_tolerance_offset": 1.5},
        {"minor_penalty": 4.0},
        {"moderate_penalty": 0.1},
    ],
)
def test_the_thresholds_refuse_values_the_rule_cannot_mean(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BGSGradingThresholds(**overrides)


def test_an_ignorance_subgrade_bgs_cannot_issue_is_refused() -> None:
    """The declared position is looked up on the ladder rather than computed from
    its step, which makes it a checked claim: a subgrade Beckett could not print
    is not a position to blend toward."""
    with pytest.raises(ValueError, match="not a grade BGS can issue"):
        predict(assessment(), thresholds=BGSGradingThresholds(unmeasured_subgrade=7.25))


def test_replacing_the_thresholds_wholesale_changes_the_answer() -> None:
    """What an experiment does. Shipping different numbers means bumping
    `GRADING_BGS_VERSION`."""
    stricter = BGSGradingThresholds(severe_penalty=6.0)
    condition = one_flawed_corner(DefectSeverity.SEVERE)

    assert mean_position(
        answered(predict(condition, thresholds=stricter)).grade_probability
    ) < mean_position(answered(predict(condition)).grade_probability)


# ----------------------------------------------------------------
# The boundary
# ----------------------------------------------------------------


def test_predicting_pulls_in_neither_opencv_nor_a_database_nor_a_store() -> None:
    """This package reads an assessment and never opens an image, so it must not
    acquire the analyzers' CV stack — a predictor that did would have silently
    rejoined the worker-only set. `ml/grading/psa`'s and `ml/grading/tag`'s
    probe, for the same reason and in the same shape."""
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_grading_bgs  # noqa: F401
        prefixes = ("cv2", "numpy", "scipy", "sqlalchemy", "asyncpg", "boto3", "botocore")
        print(json.dumps(sorted(
            name for name in sys.modules if name.startswith(prefixes)
        )))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_the_package_does_not_import_its_siblings() -> None:
    """ADR 0011: no shared `ml/grading/common`, and no sibling import — not for
    the subgrade type, not for the severity mapping, not for the normal density.
    A shared grading package is the universal condition-to-grade mapping arriving
    by the back door, and a sibling import is that package with one member.

    What the three *do* share is counting, and it lives in
    `tcg_domain.condition` (#225) — which this probe permits, because the domain
    is a dependency all three already declare.
    """
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_grading_bgs  # noqa: F401
        print(json.dumps(sorted(
            name for name in sys.modules
            if name.startswith(("tcg_ml_grading_psa", "tcg_ml_grading_tag"))
        )))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
