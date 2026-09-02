"""PSA grade prediction — spec §24, issue #223.

Needs no database, no object storage, no network and no image, so every claim
here is asserted on every push. The assessments are **built in the test**: this
package's input is a domain object, and constructing one is the honest way to
say what a card looked like.

The builders below are this file's own on purpose. `packages/domain/tests` has
an `assessment()` factory of the same shape and the two packages share no test
package — importing across would be a dependency nothing declares (#221's rule
for `REFUSED_ASSESSMENT`).
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import textwrap

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
from tcg_grading_companies.companies import PSA_SCALE
from tcg_grading_companies.port import GradePrediction
from tcg_ml_grading_psa import (
    DEFAULT_PSA_GRADING_THRESHOLDS,
    GRADING_PSA_VERSION,
    PSAGradingThresholds,
    predict,
)

# ----------------------------------------------------------------
# Builders — one valid value per shape, overridden per test
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


def scuffed(side: ImageSide, severity: DefectSeverity) -> SurfaceAssessment:
    return SurfaceAssessment(
        findings=(
            Defect(
                type=SurfaceLabel.SCUFF,
                confidence=sure(),
                severity=severity,
                side=side,
                representation=Representation.NORMALIZED,
            ),
        )
    )


def assessment(**overrides: object) -> ConditionAssessment:
    """A fully measured, undamaged card. Every test starts here."""
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


def wrecked() -> ConditionAssessment:
    """Severe damage on every axis, and centering far past the tolerance."""
    return assessment(
        centering=Centering(
            front_horizontal=0.85,
            front_vertical=0.15,
            back_horizontal=0.85,
            back_vertical=0.15,
            confidence=sure(),
        ),
        corners=dict.fromkeys(V1_SIDES, corners(CornerLabel.CHIPPING, DefectSeverity.SEVERE)),
        edges=dict.fromkeys(V1_SIDES, edges(EdgeLabel.WHITENING, DefectSeverity.SEVERE)),
        surface={side: scuffed(side, DefectSeverity.SEVERE) for side in V1_SIDES},
    )


def refused_everywhere() -> ConditionAssessment:
    """#221's `REFUSED_ASSESSMENT`: nothing was measured, and it is still an
    assessment."""
    return assessment(
        centering=INSUFFICIENT_INFORMATION,
        corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        edges=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        surface=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
        manufacturing_defects=INSUFFICIENT_INFORMATION,
        confidence=Confidence(0.0),
    )


def predicted(condition: ConditionAssessment) -> GradePrediction:
    """`predict`, with the refusal arm asserted away.

    v0.1.0 never refuses (ADR 0011 decision 1), so a test that got an
    `InsufficientInformation` back should fail here rather than at an
    attribute access three lines later.
    """
    prediction = predict(condition)
    assert isinstance(prediction, GradePrediction), prediction
    return prediction


def mass_within_one_step(distribution: GradeDistribution) -> float:
    """How much mass sits within one ladder step of the mode.

    §27's own window, and the readable way to say "wider": ±1 is a step on
    PSA's ladder, never ±1.0 of grade value.
    """
    ladder = PSA_SCALE.ordered
    peak = ladder.index(distribution.most_likely_grade)
    window = ladder[max(0, peak - 1) : peak + 2]
    return math.fsum(distribution.probability_of(grade) for grade in window)


# ----------------------------------------------------------------
# What the mapping says
# ----------------------------------------------------------------


def test_a_clean_card_and_a_wrecked_one_put_their_mass_in_different_places() -> None:
    clean = predicted(assessment()).grade_probability
    damaged = predicted(wrecked()).grade_probability

    assert clean.most_likely_grade > damaged.most_likely_grade


def test_the_distribution_covers_psas_whole_ladder_and_no_bucket() -> None:
    """ADR 0011 decision 4, and the test that fails if 9.5 ever appears."""
    distribution = predicted(assessment()).grade_probability

    PSA_SCALE.validate(distribution)
    assert tuple(distribution) == PSA_SCALE.ordered
    assert len(distribution) == 18
    assert Grade.parse("9.5") not in distribution
    assert not any(grade.is_bucket for grade in distribution)


def test_every_distribution_satisfies_section_63() -> None:
    """`GradeDistribution`'s constructor enforces it; this says the mapping
    hands it something it can accept for every shape of input."""
    for condition in (assessment(), wrecked(), refused_everywhere()):
        distribution = predicted(condition).grade_probability

        assert all(0.0 <= probability <= 1.0 for probability in distribution.probabilities.values())
        assert abs(math.fsum(distribution.probabilities.values()) - 1.0) <= SUM_TOLERANCE


def test_worse_damage_moves_the_mass_further_down_the_ladder() -> None:
    ladder = PSA_SCALE.ordered
    grades = [
        predicted(
            assessment(corners=dict.fromkeys(V1_SIDES, corners(CornerLabel.WHITENING, severity)))
        ).grade_probability.most_likely_grade
        for severity in (DefectSeverity.MINOR, DefectSeverity.MODERATE, DefectSeverity.SEVERE)
    ]

    positions = [ladder.index(grade) for grade in grades]
    assert positions == sorted(positions, reverse=True)
    assert positions[0] > positions[-1]


# ----------------------------------------------------------------
# The widening rule
# ----------------------------------------------------------------


def test_an_assessment_with_every_axis_refused_answers_rather_than_refusing() -> None:
    """ADR 0011 decision 1: *"a ConditionAssessment with every axis refused is
    still an assessment and still produces a distribution — a very wide one"*.
    There is no coverage gate and no confidence gate on this step."""
    prediction = predict(refused_everywhere())

    assert not isinstance(prediction, InsufficientInformation)
    PSA_SCALE.validate(prediction.grade_probability)


def test_a_partially_refused_assessment_is_measurably_wider() -> None:
    measured = predicted(assessment()).grade_probability
    partial = predicted(
        assessment(corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION))
    ).grade_probability
    nothing = predicted(refused_everywhere()).grade_probability

    assert mass_within_one_step(measured) > mass_within_one_step(partial)
    assert mass_within_one_step(partial) > mass_within_one_step(nothing)


def test_an_unjudgeable_corner_widens_rather_than_scoring_clean() -> None:
    """`ml/corners` answers `unknown` for a corner it cannot judge. That is
    absence of evidence, so it must not read as a clean corner — neither in the
    spread nor in where the mass sits."""
    clean = predicted(assessment()).grade_probability
    unknown = predicted(
        assessment(corners=dict.fromkeys(V1_SIDES, corners(CornerLabel.UNKNOWN)))
    ).grade_probability

    assert unknown.most_likely_grade < clean.most_likely_grade
    assert mass_within_one_step(unknown) < mass_within_one_step(clean)


def test_measuring_nothing_does_not_answer_gem_mint() -> None:
    """The blend's whole reason. An unmeasured axis contributes no damage, so
    without it a card nobody could look at would find nothing wrong with itself
    and peak at the top of the ladder — #91's rule broken in the optimistic
    direction."""
    ladder = PSA_SCALE.ordered
    nothing = predicted(refused_everywhere()).grade_probability

    assert nothing.most_likely_grade < predicted(assessment()).grade_probability.most_likely_grade
    assert ladder.index(nothing.most_likely_grade) == pytest.approx(
        (len(ladder) - 1) * DEFAULT_PSA_GRADING_THRESHOLDS.unmeasured_centre_ratio, abs=1
    )


def test_a_refused_surface_class_widens_even_though_the_side_answered() -> None:
    """ADR 0010's fine classes are refused class-level by the v0.1.0 baseline,
    so an empty `findings` tuple is not the same claim as a clean card."""
    complete = predicted(assessment()).grade_probability
    partial = predicted(
        assessment(
            surface=dict.fromkeys(
                V1_SIDES,
                SurfaceAssessment(
                    findings=(),
                    not_assessed=dict.fromkeys(
                        (SurfaceLabel.PRINT_LINE, SurfaceLabel.PRINT_DOT),
                        InsufficientInformation("class_not_assessed_by_this_baseline"),
                    ),
                ),
            )
        )
    ).grade_probability

    assert mass_within_one_step(partial) < mass_within_one_step(complete)


# ----------------------------------------------------------------
# Confidence, version, determinism
# ----------------------------------------------------------------


def test_model_confidence_never_exceeds_the_assessment_or_the_ceiling() -> None:
    """ADR 0011 decision 1's two bounds. It is not the distribution's spread."""
    ceiling = DEFAULT_PSA_GRADING_THRESHOLDS.confidence_ceiling

    assert predicted(assessment(confidence=Confidence(1.0))).model_confidence == Confidence(ceiling)
    assert predicted(assessment(confidence=Confidence(0.1))).model_confidence == Confidence(0.1)
    assert predicted(refused_everywhere()).model_confidence == Confidence(0.0)


def test_the_prediction_names_the_version_that_produced_it() -> None:
    prediction = predicted(assessment())

    assert prediction.model_version == GRADING_PSA_VERSION
    # Spec §59's bundle-name grammar, and never `latest`.
    assert re.fullmatch(r"[a-z0-9-]+-v\d+\.\d+\.\d+", prediction.model_version)
    assert "latest" not in prediction.model_version


def test_the_same_assessment_twice_gives_the_same_answer() -> None:
    first = predicted(assessment())
    second = predicted(assessment())

    assert first.grade_probability.as_mapping() == second.grade_probability.as_mapping()
    assert first.model_confidence == second.model_confidence
    assert first.model_version == second.model_version


# ----------------------------------------------------------------
# The thresholds
# ----------------------------------------------------------------


def test_thresholds_reject_a_value_the_mapping_cannot_use() -> None:
    with pytest.raises(ValueError):
        PSAGradingThresholds(corners_weight_steps=0.0)
    with pytest.raises(ValueError):
        PSAGradingThresholds(confidence_ceiling=1.5)
    with pytest.raises(ValueError):
        PSAGradingThresholds(front_centering_share=1.0)
    with pytest.raises(ValueError):
        PSAGradingThresholds(severe_damage=0.1)
    with pytest.raises(ValueError):
        PSAGradingThresholds(unmeasured_sigma_steps=-1.0)


def test_thresholds_serialise_with_the_grading_psa_prefix() -> None:
    record = DEFAULT_PSA_GRADING_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("grading_psa_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_mapping() -> None:
    """The pin: changing any threshold's value means bumping this constant.

    The `heuristic` infix is ADR 0011 decision 6's — spec §59's own
    `grading-psa-v0.2.0` names the trained bundle that replaces this one.
    """
    assert GRADING_PSA_VERSION == "grading-psa-heuristic-v0.1.0"


def test_replacing_the_thresholds_changes_the_answer() -> None:
    """The record is worth storing only if the numbers it names are what ran."""
    narrow = PSAGradingThresholds(base_sigma_steps=0.6, unmeasured_sigma_steps=0.0)
    default = predicted(assessment()).grade_probability
    tightened = predict(assessment(), thresholds=narrow)

    assert isinstance(tightened, GradePrediction)
    assert mass_within_one_step(tightened.grade_probability) > mass_within_one_step(default)


# ----------------------------------------------------------------
# The boundary
# ----------------------------------------------------------------


def test_predicting_pulls_in_neither_opencv_nor_a_database_nor_a_store() -> None:
    """This package reads an assessment and never opens an image, so it must
    not acquire the analyzers' CV stack — a predictor that did would have
    silently rejoined the worker-only set. `ml/evaluation`'s probe, for the same
    reason and in the same shape.
    """
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_grading_psa  # noqa: F401
        prefixes = ("cv2", "numpy", "sqlalchemy", "asyncpg", "boto3", "botocore")
        print(json.dumps(sorted(
            name for name in sys.modules if name.startswith(prefixes)
        )))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
