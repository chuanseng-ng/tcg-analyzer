"""TAG grade prediction — spec §24, issue #224.

Needs no database, no object storage, no network and no image, so every claim
here is asserted on every push. The assessments are **built in the test**: this
package's input is a domain object, and constructing one is the honest way to
say what a card looked like.

The builders below are this file's own on purpose, for the third time.
`packages/domain/tests` has an `assessment()` factory of the same shape and so
does `ml/grading/psa/tests`; none of the three packages share a test package, and
importing across would be a dependency nothing declares (#221's rule for
`REFUSED_ASSESSMENT`).

**Nothing here imports `tcg_ml_grading_psa`.** The comparison between the two
predictors is a repository-wide claim about spec §2.2 and lives in
`tests/test_grade_predictors_differ.py`, which is what keeps it from costing this
package a dependency on its sibling.
"""

from __future__ import annotations

import itertools
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
from tcg_grading_companies.companies import TAG_SCALE
from tcg_grading_companies.port import GradePrediction
from tcg_ml_grading_tag import (
    DEFAULT_TAG_GRADING_THRESHOLDS,
    GRADING_TAG_VERSION,
    TAG_SCORE_MAXIMUM,
    TAGGradingThresholds,
    predict,
)

# The band table is the shape claim rather than an implementation detail of it,
# so it is asserted directly. Nothing else reaches past the public surface.
from tcg_ml_grading_tag.predictor import _band_bounds

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
    """A fully scanned, undamaged card. Every test starts here."""
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
    """Severe damage on every category, and centering far past the tolerance."""
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
    """#221's `REFUSED_ASSESSMENT`: nothing was scanned, and it is still an
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
    `InsufficientInformation` back should fail here rather than at an attribute
    access three lines later.
    """
    prediction = predict(condition)
    assert isinstance(prediction, GradePrediction), prediction
    return prediction


def ladder_spread(distribution: GradeDistribution) -> float:
    """How spread out the answer is, as Shannon entropy over the ladder.

    **PSA's `mass_within_one_step` deliberately does not transfer**, and the
    substitution is itself evidence the two mappings are shaped differently. A
    fixed three-grade window measures spread only where the model's own units
    make the steps comparable; TAG's bands are 69 score points wide at the bottom
    of the ladder and 27 at the top, so the same window means different things at
    different grades. Entropy asks the question the window was standing in for
    and does not care how wide a band is.
    """
    return -math.fsum(
        probability * math.log(probability)
        for probability in distribution.probabilities.values()
        if probability > 0.0
    )


# ----------------------------------------------------------------
# What the mapping says
# ----------------------------------------------------------------


def test_a_clean_card_and_a_wrecked_one_put_their_mass_in_different_places() -> None:
    clean = predicted(assessment()).grade_probability
    damaged = predicted(wrecked()).grade_probability

    assert clean.most_likely_grade > damaged.most_likely_grade


def test_the_distribution_covers_tags_whole_ladder_and_no_bucket() -> None:
    """ADR 0011 decision 4, and the test that fails if 9.5 ever appears — TAG has
    no more of one than PSA does."""
    distribution = predicted(assessment()).grade_probability

    TAG_SCALE.validate(distribution)
    assert tuple(distribution) == TAG_SCALE.ordered
    assert len(distribution) == 18
    assert Grade.parse("9.5") not in distribution
    assert not any(grade.is_bucket for grade in distribution)


def test_a_flawless_card_is_graded_ten_and_never_a_designation() -> None:
    """TAG issues grade 10 under two names, Pristine and Gem Mint, and
    `companies.py` is explicit that *"the prediction side does not read them and
    must not start"*. The top of this ladder is one point, and the keys are
    grades — a designation could not be one of them even by accident."""
    distribution = predicted(assessment()).grade_probability

    assert distribution.most_likely_grade == Grade.parse("10")
    assert {str(grade) for grade in distribution}.isdisjoint({"pristine_10", "gem_mint_10"})


def test_every_distribution_satisfies_section_63() -> None:
    """`GradeDistribution`'s constructor enforces it; this says the mapping hands
    it something it can accept for every shape of input — including the
    truncated integral's negative epsilons."""
    for condition in (assessment(), wrecked(), refused_everywhere()):
        distribution = predicted(condition).grade_probability

        assert all(0.0 <= probability <= 1.0 for probability in distribution.probabilities.values())
        assert abs(math.fsum(distribution.probabilities.values()) - 1.0) <= SUM_TOLERANCE


def test_worse_damage_moves_the_mass_further_down_the_ladder() -> None:
    ladder = TAG_SCALE.ordered
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
# The band table — the shape claim itself
# ----------------------------------------------------------------


def test_the_band_table_is_monotone_and_covers_the_whole_scale() -> None:
    bounds = _band_bounds(DEFAULT_TAG_GRADING_THRESHOLDS.band_curvature, len(TAG_SCALE.ordered))

    assert len(bounds) == len(TAG_SCALE.ordered) + 1
    assert bounds[0] == 0.0
    assert bounds[-1] == TAG_SCORE_MAXIMUM
    assert list(bounds) == sorted(bounds)
    assert len(set(bounds)) == len(bounds)


def test_the_bands_are_not_uniform_and_tighten_toward_the_top() -> None:
    """The whole differentiator, asserted on the mechanism rather than inferred
    from an output. Uniform bands would make the score-to-grade placement linear,
    which is what would make this predictor PSA's in different units."""
    bounds = _band_bounds(DEFAULT_TAG_GRADING_THRESHOLDS.band_curvature, len(TAG_SCALE.ordered))
    widths = [upper - lower for lower, upper in itertools.pairwise(bounds)]

    assert widths == sorted(widths, reverse=True)
    assert widths[0] > widths[-1] * 2.0


def test_a_curvature_of_one_would_make_the_bands_uniform() -> None:
    """The null case, stated so the constant's meaning cannot drift: 1.0 is the
    linear mapping this predictor exists not to be."""
    bounds = _band_bounds(1.0, 18)
    widths = [upper - lower for lower, upper in itertools.pairwise(bounds)]

    assert widths == pytest.approx([TAG_SCORE_MAXIMUM / 18] * 18)


# ----------------------------------------------------------------
# The widening rule
# ----------------------------------------------------------------


def test_an_assessment_with_every_category_refused_answers_rather_than_refusing() -> None:
    """ADR 0011 decision 1: *"a ConditionAssessment with every axis refused is
    still an assessment and still produces a distribution — a very wide one"*.
    There is no coverage gate and no confidence gate on this step."""
    prediction = predict(refused_everywhere())

    assert not isinstance(prediction, InsufficientInformation)
    TAG_SCALE.validate(prediction.grade_probability)


def test_a_partially_refused_assessment_is_measurably_wider() -> None:
    measured = predicted(assessment()).grade_probability
    partial = predicted(
        assessment(corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION))
    ).grade_probability
    nothing = predicted(refused_everywhere()).grade_probability

    assert ladder_spread(measured) < ladder_spread(partial)
    assert ladder_spread(partial) < ladder_spread(nothing)


def test_an_unreadable_corner_widens_rather_than_scoring_clean() -> None:
    """`ml/corners` answers `unknown` for a corner it cannot judge. That is
    absence of evidence, so it must not read as a clean corner — neither in the
    spread nor in where the mass sits."""
    clean = predicted(assessment()).grade_probability
    unknown = predicted(
        assessment(corners=dict.fromkeys(V1_SIDES, corners(CornerLabel.UNKNOWN)))
    ).grade_probability

    assert unknown.most_likely_grade < clean.most_likely_grade
    assert ladder_spread(unknown) > ladder_spread(clean)


def test_scanning_nothing_does_not_answer_pristine() -> None:
    """The blend's whole reason, and #223's guard in TAG's units. An uncovered
    category deducts nothing, so without the blend a card no instrument could
    read would keep a full 1000 and peak at the top of the ladder — #91's rule
    broken in the optimistic direction.

    The truncation of the score distribution to ``[0, 1000]`` is the second half
    of the same guard: an unbounded top tail hands this card about a fifth of its
    mass on grade 10 whatever the centre does.
    """
    nothing = predicted(refused_everywhere()).grade_probability
    ladder = TAG_SCALE.ordered

    assert nothing.most_likely_grade < predicted(assessment()).grade_probability.most_likely_grade
    assert nothing.most_likely_grade > predicted(wrecked()).grade_probability.most_likely_grade
    assert nothing.probability_of(ladder[-1]) < 0.05


def test_a_refused_surface_class_widens_even_though_the_side_answered() -> None:
    """ADR 0010's fine classes are refused class-level by the v0.1.0 baseline, so
    an empty `findings` tuple is not the same claim as a clean card."""
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

    assert ladder_spread(partial) > ladder_spread(complete)


# ----------------------------------------------------------------
# Confidence, version, determinism
# ----------------------------------------------------------------


def test_model_confidence_never_exceeds_the_assessment_or_the_ceiling() -> None:
    """ADR 0011 decision 1's two bounds. It is not the distribution's spread."""
    ceiling = DEFAULT_TAG_GRADING_THRESHOLDS.confidence_ceiling

    assert predicted(assessment(confidence=Confidence(1.0))).model_confidence == Confidence(ceiling)
    assert predicted(assessment(confidence=Confidence(0.1))).model_confidence == Confidence(0.1)
    assert predicted(refused_everywhere()).model_confidence == Confidence(0.0)


def test_the_prediction_names_the_version_that_produced_it() -> None:
    prediction = predicted(assessment())

    assert prediction.model_version == GRADING_TAG_VERSION
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
        TAGGradingThresholds(corners_weight=0.0)
    with pytest.raises(ValueError):
        TAGGradingThresholds(band_curvature=0.0)
    with pytest.raises(ValueError):
        TAGGradingThresholds(confidence_ceiling=1.5)
    with pytest.raises(ValueError):
        TAGGradingThresholds(centering_tolerance_offset=0.0)
    with pytest.raises(ValueError):
        TAGGradingThresholds(unmeasured_score=1200.0)
    with pytest.raises(ValueError):
        TAGGradingThresholds(severe_deduction=10.0)
    with pytest.raises(ValueError):
        TAGGradingThresholds(unmeasured_sigma_score=-1.0)


def test_thresholds_serialise_with_the_grading_tag_prefix() -> None:
    record = DEFAULT_TAG_GRADING_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("grading_tag_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_mapping() -> None:
    """The pin: changing any threshold's value means bumping this constant.

    The `heuristic` infix is ADR 0011 decision 6's — spec §59's grammar reserves
    `grading-tag-v0.2.0` for the trained bundle that replaces this one.
    """
    assert GRADING_TAG_VERSION == "grading-tag-heuristic-v0.1.0"


def test_replacing_the_thresholds_changes_the_answer() -> None:
    """The record is worth storing only if the numbers it names are what ran."""
    narrow = TAGGradingThresholds(base_sigma_score=10.0, unmeasured_sigma_score=0.0)
    default = predicted(assessment()).grade_probability
    tightened = predict(assessment(), thresholds=narrow)

    assert isinstance(tightened, GradePrediction)
    assert ladder_spread(tightened.grade_probability) < ladder_spread(default)


# ----------------------------------------------------------------
# The boundary
# ----------------------------------------------------------------


def test_predicting_pulls_in_neither_opencv_nor_a_database_nor_a_store() -> None:
    """This package reads an assessment and never opens an image, so it must not
    acquire the analyzers' CV stack — a predictor that did would have silently
    rejoined the worker-only set. `ml/grading/psa`'s probe, for the same reason
    and in the same shape.

    The probe also covers the arithmetic: `math.erf` is stdlib, and reaching for
    scipy or numpy for one closed form would show up here.
    """
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_grading_tag  # noqa: F401
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
    the category type, not for the severity mapping, not for the normal density.
    A shared grading package is the universal condition-to-grade mapping arriving
    by the back door, and a sibling import is that package with one member.
    """
    probe = textwrap.dedent(
        """
        import json
        import sys

        import tcg_ml_grading_tag  # noqa: F401
        print(json.dumps(sorted(
            name for name in sys.modules
            if name.startswith(("tcg_ml_grading_psa", "tcg_ml_grading_bgs"))
        )))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
