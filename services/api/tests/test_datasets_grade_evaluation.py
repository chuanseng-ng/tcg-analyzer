"""The grade runner's pure seams — one subject per copy, the envelope (#242).

The IO orchestration is `tcg_api.datasets.evaluation.analyze_version`'s and
is exercised against the live corpus by hand; what is asserted here is
everything that decides what a committed grade record says: which copy
becomes a subject, what its three predictions are when it cannot be
composed, and what the §61 envelope carries.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from tcg_api.datasets import grade_evaluation
from tcg_api.datasets.evaluation import AnalyzedVersion
from tcg_domain.analysis import V1_SIDES
from tcg_domain.annotation import CornerLabel, CornerRegion, EdgeLabel, EdgeRegion
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    RegionFinding,
    SurfaceAssessment,
)
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence, InsufficientInformation
from tcg_domain.dataset import DatasetSplit
from tcg_domain.grade import Grade
from tcg_grading_companies import GradePrediction, GradePredictionFailed
from tcg_ml_evaluation import EvaluationCorpus
from tcg_ml_evaluation.grading import GRADE_EVALUATION_VERSION, IssuedGrade
from tcg_ml_evaluation.manifest import CorpusMember, CorpusOutcome

FRONT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
BACK = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
COPY = uuid.UUID("00000000-0000-0000-0000-00000000c0c0")
COMPANIES = {"bgs", "psa", "tag"}


def _assessment() -> ConditionAssessment:
    """A fully measured, undamaged card."""
    sure = Confidence(0.9)
    return ConditionAssessment(
        centering=Centering(
            front_horizontal=0.5,
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure,
        ),
        corners={
            side: {
                region: RegionFinding(label=CornerLabel.CLEAN, confidence=sure)
                for region in CornerRegion
            }
            for side in V1_SIDES
        },
        edges={
            side: {
                region: RegionFinding(label=EdgeLabel.CLEAN, confidence=sure)
                for region in EdgeRegion
            }
            for side in V1_SIDES
        },
        surface=dict.fromkeys(V1_SIDES, SurfaceAssessment(findings=())),
        manufacturing_defects=(),
        eye_appeal=INSUFFICIENT_INFORMATION,
        confidence=sure,
    )


def _member(
    image_id: uuid.UUID, side: str, *, outcomes: tuple[CorpusOutcome, ...] = ()
) -> CorpusMember:
    return CorpusMember(
        training_image_id=image_id,
        sha256=str(image_id).replace("-", "")[:2] * 32,
        split=DatasetSplit.VALIDATION,
        side=side,
        source="first_party",
        acquisition_method="photographed_before_submission",
        original_uri=f"training/{image_id}.png",
        annotations=(),
        centering=(),
        grading_outcomes=outcomes,
    )


PSA_NINE = CorpusOutcome(
    id=uuid.UUID("00000000-0000-0000-0000-00000000000f"),
    company="psa",
    certification_number="12345678",
    grade=Grade.parse("9"),
    designation=None,
    created_at=datetime(2026, 9, 5, tzinfo=UTC),
)


def _analyzed(
    *,
    sides: dict[uuid.UUID, tuple[str, uuid.UUID | None]] | None = None,
    outputs: dict[uuid.UUID, object] | None = None,
    excluded: dict[uuid.UUID, str] | None = None,
) -> AnalyzedVersion:
    corpus = EvaluationCorpus(
        dataset_version="pokemon-condition-v0.2.0",
        split_seed=1,
        members=(
            _member(FRONT, "front", outcomes=(PSA_NINE,)),
            _member(BACK, "back", outcomes=(PSA_NINE,)),
        ),
    )
    if sides is None:
        sides = {FRONT: ("front", COPY), BACK: ("back", COPY)}
    if outputs is None:
        outputs = {image_id: object() for image_id in sides}
    return AnalyzedVersion(
        corpus,
        sides,
        outputs,  # type: ignore[arg-type]  # `compose_pair` is patched; the shape is never read
        {},
        excluded or {},
    )


def test_a_composed_copy_is_one_subject_predicted_by_all_three_companies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(grade_evaluation, "compose_pair", lambda _front, _back: _assessment())

    subjects = grade_evaluation.subjects_of(_analyzed())

    (subject,) = subjects
    assert subject.subject_id == COPY
    assert subject.split == DatasetSplit.VALIDATION
    assert set(subject.predictions) == COMPANIES
    assert all(isinstance(p, GradePrediction) for p in subject.predictions.values())
    assert subject.outcomes == {"psa": IssuedGrade(company="psa", grade=Grade.parse("9"))}


def test_an_excluded_side_refuses_all_three_companies_before_any_model_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0011 decision 1: the only refusal is on the way in, in the worker's vocabulary."""

    def never(front: object, back: object) -> object:
        raise AssertionError("nothing to compose")

    monkeypatch.setattr(grade_evaluation, "compose_pair", never)

    (subject,) = grade_evaluation.subjects_of(
        _analyzed(outputs={FRONT: object()}, excluded={BACK: "no_card_frame"})
    )

    assert subject.predictions == dict.fromkeys(
        COMPANIES, InsufficientInformation("no_card_frame_for_back")
    )


def test_a_refused_composition_is_the_three_companies_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grade_evaluation,
        "compose_pair",
        lambda _front, _back: InsufficientInformation("no_axis_measured"),
    )

    (subject,) = grade_evaluation.subjects_of(_analyzed())

    assert subject.predictions == dict.fromkeys(
        COMPANIES, InsufficientInformation("no_axis_measured")
    )


def test_a_copy_missing_a_side_is_counted_not_composed() -> None:
    (subject,) = grade_evaluation.subjects_of(_analyzed(sides={FRONT: ("front", COPY)}))

    assert subject.predictions == dict.fromkeys(COMPANIES, InsufficientInformation("no_back_image"))


def test_an_image_naming_no_copy_is_no_subject() -> None:
    assert grade_evaluation.subjects_of(_analyzed(sides={FRONT: ("front", None)})) == []


def test_a_model_that_raised_is_that_company_s_refusal_not_the_run_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#226: a caller catches `GradingCompanyError`, never `except Exception`."""

    class Broken:
        def predict_grade(self, condition: ConditionAssessment) -> object:
            raise GradePredictionFailed("psa: the model raised")

    monkeypatch.setattr(grade_evaluation, "PREDICTING_ADAPTERS", {"psa": Broken()})

    assert grade_evaluation.predict_or_refuse("psa", _assessment()) == InsufficientInformation(
        "predictor_error"
    )


def test_the_envelope_carries_every_constant_the_numbers_came_through() -> None:
    """Spec §61, full provenance: the run re-runs the four analyzers to reach a
    `ConditionAssessment` and writes no worker document, so nothing else records
    what produced the predictions."""
    report = {"dataset_version": "pokemon-condition-v0.2.0"}

    text = grade_evaluation.render_experiment(report, commit="abc123")

    payload = json.loads(text)
    assert payload["dataset_version"] == "pokemon-condition-v0.2.0"
    assert payload["git_commit"] == "abc123"
    assert payload["condition_version"].startswith("condition-compose-")
    assert payload["grading_version"].startswith("grading-bgs-")
    assert payload["analyzer_thresholds"]["surface_stain_max_value"] is not None
    assert payload["predictor_thresholds"]["grading_psa_base_sigma_steps"] is not None
    assert "hardware" not in payload
    assert text.endswith("\n")
    assert text == grade_evaluation.render_experiment(report, commit="abc123")


def test_the_record_lands_in_the_grade_family() -> None:
    path = grade_evaluation.record_path("pokemon-condition-v0.2.0")

    assert path.name == f"pokemon-condition-v0.2.0+{GRADE_EVALUATION_VERSION}.json"
