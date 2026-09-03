"""Running and recording the grade prediction step — issue #227, spec §24, §63.

Two halves, `test_analysis_condition.py`'s split. The wiring half needs no
database: `predict_grades` reads one stored document, rehydrates the
assessment, hands it to each company's adapter and writes one document — a
recorder standing in for the two statements is a truthful stand-in for all of
it, and the predictors are the real ones. The persistence half needs real
PostgreSQL, because what `record_grade_predictions` and `read_condition` have
to get right is that a JSONB document round-trips and that a NULL comes back
as `None`.

The rules under test everywhere: **a step that runs always writes a
document**, so NULL keeps meaning "the step never ran"; **a refusal is a
stored value**, never an absence; and **an invalid distribution never reaches
the database** (§63).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis import grading
from tcg_api.analysis.sessions import read_condition, record_condition, record_grade_predictions
from tcg_api.analysis.tables import analyses
from tcg_api.database import create_session_factory
from tcg_api.version import application_version
from tcg_domain.analysis import ImageSide
from tcg_domain.annotation import CornerLabel, CornerRegion, EdgeLabel, EdgeRegion
from tcg_domain.condition import (
    Centering,
    ConditionAssessment,
    RegionFinding,
    SurfaceAssessment,
)
from tcg_domain.confidence import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    InsufficientInformation,
)
from tcg_domain.distribution import GradeDistribution
from tcg_domain.grade import Grade
from tcg_grading_companies import (
    ADAPTERS,
    BGS_SCALE,
    PSA_SCALE,
    TAG_SCALE,
    GradePrediction,
    GradePredictionFailed,
    PSAAdapter,
)
from tcg_ml_condition import CONDITION_VERSION
from tcg_ml_grading_bgs import DEFAULT_BGS_GRADING_THRESHOLDS, GRADING_BGS_VERSION
from tcg_ml_grading_psa import DEFAULT_PSA_GRADING_THRESHOLDS, GRADING_PSA_VERSION
from tcg_ml_grading_tag import DEFAULT_TAG_GRADING_THRESHOLDS, GRADING_TAG_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

SCALES = {"bgs": BGS_SCALE, "psa": PSA_SCALE, "tag": TAG_SCALE}
VERSIONS = {"bgs": GRADING_BGS_VERSION, "psa": GRADING_PSA_VERSION, "tag": GRADING_TAG_VERSION}


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def an_assessment() -> ConditionAssessment:
    """A minimal real assessment — the record the condition step would have written."""
    sure = Confidence(0.9)
    corners = {
        side: {
            region: RegionFinding(label=CornerLabel.CLEAN, confidence=sure)
            for region in CornerRegion
        }
        for side in (ImageSide.FRONT, ImageSide.BACK)
    }
    edges = {
        side: {
            region: RegionFinding(label=EdgeLabel.CLEAN, confidence=sure) for region in EdgeRegion
        }
        for side in (ImageSide.FRONT, ImageSide.BACK)
    }
    return ConditionAssessment(
        centering=Centering(
            front_horizontal=0.55,
            front_vertical=0.48,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure,
        ),
        corners=corners,
        edges=edges,
        surface={
            ImageSide.FRONT: SurfaceAssessment(findings=()),
            ImageSide.BACK: SurfaceAssessment(findings=()),
        },
        manufacturing_defects=(),
        eye_appeal=INSUFFICIENT_INFORMATION,
        confidence=Confidence(0.8),
    )


def a_condition_document(**outcome: Any) -> dict[str, Any]:
    """What `assess_condition` stored: version, thresholds, then the outcome."""
    return {"version": CONDITION_VERSION, "thresholds": {}, **outcome}


#: Every predictor's defaults, merged — prefixed, so the merge cannot collide.
MERGED_THRESHOLDS = {
    **DEFAULT_PSA_GRADING_THRESHOLDS.as_record(),
    **DEFAULT_TAG_GRADING_THRESHOLDS.as_record(),
    **DEFAULT_BGS_GRADING_THRESHOLDS.as_record(),
}


# ---------------------------------------------------------------------------
# The wiring — no database, no OpenCV, no network
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for the two statements `predict_grades` issues."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document
        self.documents: list[dict[str, Any]] = []

    async def read_condition(self, _db: Any, _analysis_id: UUID) -> dict[str, Any] | None:
        return self.document

    async def record_grade_predictions(
        self, _db: Any, _analysis_id: UUID, *, details: dict[str, Any]
    ) -> None:
        self.documents.append(details)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recorder]:
    """`predict_grades` with its two statements replaced and the real predictors."""
    recorder = _Recorder(a_condition_document(assessment=an_assessment().as_record()))
    monkeypatch.setattr(grading, "read_condition", recorder.read_condition)
    monkeypatch.setattr(grading, "record_grade_predictions", recorder.record_grade_predictions)
    yield recorder


def _with_psa(monkeypatch: pytest.MonkeyPatch, predictor: Any) -> None:
    """Swap PSA's model for a stub, keeping the real adapter class in front of it."""
    adapters = {**grading.PREDICTING_ADAPTERS, "psa": PSAAdapter(predictor=predictor)}
    monkeypatch.setattr(grading, "PREDICTING_ADAPTERS", adapters)


def test_each_company_answers_over_its_own_full_ladder(wired: _Recorder) -> None:
    """M8's acceptance criterion in the product: per company, a full
    distribution (18 / 18 / 19 keys, no bucket — ADR 0011 decision 4) that
    reads back through `GradeDistribution.from_mapping`, validates against
    **that** company's scale, and carries that model's confidence and version.
    """
    run(lambda: grading.predict_grades(None, uuid.uuid4()))

    predictions = wired.documents[0]["predictions"]
    assert set(predictions) == set(ADAPTERS)
    for slug, entry in predictions.items():
        distribution = GradeDistribution.from_mapping(entry["distribution"])
        SCALES[slug].validate(distribution)
        assert len(distribution.probabilities) == len(SCALES[slug].grades)
        assert 0.0 <= entry["model_confidence"] <= 1.0
        assert entry["model_version"] == VERSIONS[slug]
    assert (
        Grade.parse("9.5")
        in GradeDistribution.from_mapping(predictions["bgs"]["distribution"]).probabilities
    )
    assert "9.5" not in predictions["psa"]["distribution"]


def test_the_document_is_the_predictions_beside_the_version_and_the_thresholds(
    wired: _Recorder,
) -> None:
    """`condition_details`' shape one stage on: the composed grading version
    and the three predictors' `as_record()`s, then the outcome — a row explains
    itself."""
    run(lambda: grading.predict_grades(None, uuid.uuid4()))

    document = wired.documents[0]
    assert tuple(document) == ("version", "thresholds", "predictions")
    assert document["version"] == grading.GRADING_VERSION
    assert document["thresholds"] == MERGED_THRESHOLDS
    assert tuple(document["predictions"]) == tuple(sorted(ADAPTERS))


def test_a_refused_condition_document_is_three_stored_refusals(wired: _Recorder) -> None:
    """ADR 0011 decision 1: the only refusal is on the way in. A top-level
    `insufficient_information` propagates to every company with its reason —
    stored, never absent, and the type is never built."""
    wired.document = a_condition_document(insufficient_information="no_axis_measured")

    run(lambda: grading.predict_grades(None, uuid.uuid4()))

    assert wired.documents[0]["predictions"] == {
        slug: {"insufficient_information": "no_axis_measured"} for slug in ADAPTERS
    }


def test_a_missing_condition_document_is_three_stored_refusals(wired: _Recorder) -> None:
    """A caller that skipped the condition step still gets a document here, so
    NULL keeps its one meaning for this column too."""
    wired.document = None

    run(lambda: grading.predict_grades(None, uuid.uuid4()))

    assert wired.documents[0]["predictions"] == {
        slug: {"insufficient_information": "condition_step_not_run"} for slug in ADAPTERS
    }


def test_a_model_that_declines_is_a_stored_refusal_beside_the_others(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model's `InsufficientInformation` is a result (#226), and it is
    persisted as itself — the other two companies still answer."""
    _with_psa(monkeypatch, lambda _assessment: InsufficientInformation("not_this_card"))

    run(lambda: grading.predict_grades(None, uuid.uuid4()))

    predictions = wired.documents[0]["predictions"]
    assert predictions["psa"] == {"insufficient_information": "not_this_card"}
    assert "distribution" in predictions["tag"]
    assert "distribution" in predictions["bgs"]


def test_an_invalid_distribution_is_refused_at_the_boundary_and_never_stored(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §63: "the API should reject invalid model output." `GradeDistribution`
    is §63 and the adapter checks the ladder; what neither can see is a swapped
    model handing back a `GradePrediction` whose distribution is not that type
    at all — a dataclass does not check. The wiring refuses it, and nothing
    reaches the store."""
    bogus = GradePrediction(
        grade_probability={Grade.parse("10"): 1.2},  # type: ignore[arg-type]
        model_confidence=Confidence(0.3),
        model_version="grading-psa-bogus-v0",
    )
    _with_psa(monkeypatch, lambda _assessment: bogus)

    with pytest.raises(GradePredictionFailed):
        run(lambda: grading.predict_grades(None, uuid.uuid4()))

    assert wired.documents == []


def test_a_model_that_breaks_fails_the_job_rather_than_recording_a_refusal(
    wired: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that broke must never be recorded as a model that declined. The
    adapter translates the failure (#226) and the wiring lets it propagate to
    the job runner's retry path."""

    def broken(_assessment: ConditionAssessment) -> Any:
        raise RuntimeError("weights missing")

    _with_psa(monkeypatch, broken)

    with pytest.raises(GradePredictionFailed):
        run(lambda: grading.predict_grades(None, uuid.uuid4()))

    assert wired.documents == []


def test_the_predicting_adapters_are_the_registry_with_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#226's rule: the same three classes, keyed by the same slugs, each
    constructed with its model — and `ADAPTERS` itself still refuses."""
    from tcg_grading_companies import GradePredictionUnavailable

    assert set(grading.PREDICTING_ADAPTERS) == set(ADAPTERS)
    for slug, adapter in grading.PREDICTING_ADAPTERS.items():
        assert type(adapter) is type(ADAPTERS[slug])
    with pytest.raises(GradePredictionUnavailable):
        ADAPTERS["psa"].predict_grade(an_assessment())


def test_the_outcome_is_logged_with_the_analysis_it_belongs_to(
    wired: _Recorder, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One line, structlog keywords: the identifier, the version and which
    companies refused — never a probability (spec §54)."""
    from tcg_api.config import Settings
    from tcg_api.logging import configure_logging

    configure_logging(Settings(log_format="json"))
    _with_psa(monkeypatch, lambda _assessment: InsufficientInformation("not_this_card"))
    analysis_id = uuid.uuid4()

    run(lambda: grading.predict_grades(None, analysis_id))

    written = capsys.readouterr()
    lines = [
        entry
        for entry in (written.out + written.err).splitlines()
        if "analysis.grades_predicted" in entry
    ]
    assert len(lines) == 1
    assert str(analysis_id) in lines[0]
    assert grading.GRADING_VERSION in lines[0]
    assert "not_this_card" in lines[0]
    assert "distribution" not in lines[0]


# ---------------------------------------------------------------------------
# The write and the read, against PostgreSQL
# ---------------------------------------------------------------------------

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base`, and pytest promises no order."""
    if not DATABASE_URL:
        return
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def analysis() -> Iterator[UUID]:
    """One analysis under one session, deleted afterwards."""
    session_id, analysis_id = uuid.uuid4(), uuid.uuid4()

    async def insert() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "INSERT INTO analysis_sessions (id, anonymous_session_id, expires_at, "
                        "application_version) VALUES (:id, :token, now() + interval '1 day', :v)"
                    ).bindparams(id=session_id, token=str(session_id), v=application_version())
                )
                await connection.execute(
                    sa.text(
                        "INSERT INTO analyses (id, session_id) VALUES (:id, :session_id)"
                    ).bindparams(id=analysis_id, session_id=session_id)
                )
        finally:
            await engine.dispose()

    async def delete() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("DELETE FROM analysis_sessions WHERE id = :id").bindparams(
                        id=session_id
                    )
                )
        finally:
            await engine.dispose()

    run(insert)
    try:
        yield analysis_id
    finally:
        run(delete)


async def _with_session[T](scenario: Callable[[Any], Awaitable[T]]) -> T:
    engine = create_async_engine(DATABASE_URL or "")
    try:
        async with create_session_factory(engine)() as db:
            result = await scenario(db)
            await db.commit()
            return result
    finally:
        await engine.dispose()


@pytest.mark.integration
@requires_postgres
def test_the_predictions_document_round_trips_through_jsonb(analysis: UUID) -> None:
    document = {
        "version": grading.GRADING_VERSION,
        "thresholds": MERGED_THRESHOLDS,
        "predictions": {"psa": {"insufficient_information": "no_axis_measured"}},
    }

    async def write(db: Any) -> None:
        await record_grade_predictions(db, analysis, details=document)

    async def read(db: Any) -> Any:
        result = await db.execute(
            sa.select(analyses.c.grade_predictions).where(analyses.c.id == analysis)
        )
        return result.scalar_one()

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(read)) == document


@pytest.mark.integration
@requires_postgres
def test_the_condition_document_reads_back_and_a_null_is_none(analysis: UUID) -> None:
    """The seam #187 left, read from this side: the whole document, and a
    step that never ran is `None` rather than an empty mapping."""
    document = a_condition_document(assessment=an_assessment().as_record())

    async def before(db: Any) -> Any:
        return await read_condition(db, analysis)

    async def write_then_read(db: Any) -> Any:
        await record_condition(db, analysis, details=document)
        return await read_condition(db, analysis)

    assert run(lambda: _with_session(before)) is None
    assert run(lambda: _with_session(write_then_read)) == document
