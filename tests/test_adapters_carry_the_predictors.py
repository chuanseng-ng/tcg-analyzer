"""The three adapters answer spec §22's fifth responsibility with the three predictors.

#226's acceptance criterion, with the real models rather than stubs.
`packages/grading-companies` proves the seam with stubs, because a test there
importing `tcg_ml_grading_psa` would be a dependency nothing declares — the
package depends on `tcg-domain` alone, and ADR 0011 decision 5 fixes the
direction as `ml/grading/*` -> `packages/grading-companies`, never the reverse.
So the one place that may bind all three predictors to all three adapters is
here, beside `test_grade_predictors_differ.py` and for the same reason: a claim
about the repository rather than about a package.

What is asserted is exactly what the port promises: an adapter built with its
company's predictor returns a `GradePrediction` over that company's own ladder,
stamped with that predictor's version. Which grade it answers is the
predictor's business and its own package's tests.

The worker's wiring — which module builds these adapters, and where the answer
is stored — is #227's. Nothing here reaches a database, an object store or an
image.
"""

from __future__ import annotations

import pytest
from tcg_domain.analysis import V1_SIDES
from tcg_domain.annotation import CornerLabel, CornerRegion, EdgeLabel, EdgeRegion
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    RegionFinding,
    SurfaceAssessment,
)
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence
from tcg_grading_companies import (
    ADAPTERS,
    BGSAdapter,
    GradePrediction,
    GradePredictionUnavailable,
    GradingCompanyAdapter,
    PSAAdapter,
    TAGAdapter,
)
from tcg_ml_grading_bgs import GRADING_BGS_VERSION
from tcg_ml_grading_bgs import predict as predict_bgs
from tcg_ml_grading_psa import GRADING_PSA_VERSION
from tcg_ml_grading_psa import predict as predict_psa
from tcg_ml_grading_tag import GRADING_TAG_VERSION
from tcg_ml_grading_tag import predict as predict_tag

#: Each company's adapter, built the way #227's worker will build it. A fourth
#: company is one more line here and one more adapter — nothing else.
PREDICTING_ADAPTERS: tuple[tuple[GradingCompanyAdapter, str], ...] = (
    (PSAAdapter(predictor=predict_psa), GRADING_PSA_VERSION),
    (TAGAdapter(predictor=predict_tag), GRADING_TAG_VERSION),
    (BGSAdapter(predictor=predict_bgs), GRADING_BGS_VERSION),
)


def _sure() -> Confidence:
    return Confidence(0.9)


def _assessment() -> ConditionAssessment:
    """A fully measured, undamaged card — the fifth copy of this builder.

    Deliberately not imported from `test_grade_predictors_differ.py`: the root
    tests are files, not a package, and each states its own input.
    """
    return ConditionAssessment(
        centering=Centering(
            front_horizontal=0.5,
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=_sure(),
        ),
        corners={
            side: {
                region: RegionFinding(label=CornerLabel.CLEAN, confidence=_sure())
                for region in CornerRegion
            }
            for side in V1_SIDES
        },
        edges={
            side: {
                region: RegionFinding(label=EdgeLabel.CLEAN, confidence=_sure())
                for region in EdgeRegion
            }
            for side in V1_SIDES
        },
        surface=dict.fromkeys(V1_SIDES, SurfaceAssessment(findings=())),
        manufacturing_defects=(),
        eye_appeal=INSUFFICIENT_INFORMATION,
        confidence=Confidence(0.9),
    )


@pytest.mark.parametrize(
    ("adapter", "version"), PREDICTING_ADAPTERS, ids=[a.company for a, _ in PREDICTING_ADAPTERS]
)
def test_an_adapter_built_with_its_predictor_answers_on_its_own_ladder(
    adapter: GradingCompanyAdapter, version: str
) -> None:
    prediction = adapter.predict_grade(_assessment())

    assert isinstance(prediction, GradePrediction)
    assert prediction.model_version == version
    adapter.get_grade_scale().validate(prediction.grade_probability)
    assert set(prediction.grade_probability) == adapter.get_grade_scale().grades


def test_the_registry_the_api_image_imports_still_carries_no_model() -> None:
    """Injection, not import: `ADAPTERS` refuses exactly as before #226."""
    for adapter in ADAPTERS.values():
        with pytest.raises(GradePredictionUnavailable):
            adapter.predict_grade(_assessment())
