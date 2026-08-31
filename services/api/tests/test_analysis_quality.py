"""Running and recording the CV stages — issues #36, #37, #38, spec §18, §19.

Two halves, split by what each actually needs.

The first needs nothing: `prepare_images` reads bytes from a port, folds two
verdicts into one and decides which photographs get straightened, and
`InMemoryObjectStorage` plus stubbed stages is a truthful stand-in for all of
it. What is asserted there is the fold, the fact that the photographs are read
from storage rather than from anywhere else, and the three cases in which no
normalized artifact is produced.

The second needs real PostgreSQL, because what `record_quality` and
`record_normalization` have to get right is that a JSONB document round-trips,
that the `[0, 1]` CHECK the migration added actually refuses a bad score, and
that a retake clears what the stages wrote. A fake that answered those in
Python would be testing the fake — the argument `test_analysis_state.py` makes
at length.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis import quality
from tcg_api.analysis.images import (
    CachedPipelineResult,
    apply_cached_pipeline_result,
    read_cached_pipeline_result,
    read_image_objects,
    read_quality,
    record_normalization,
    record_quality,
    upsert_image,
)
from tcg_api.database import create_session_factory
from tcg_api.version import application_version
from tcg_domain.analysis import ImageSide, QualityStatus
from tcg_domain.card_geometry import CardGeometry
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence
from tcg_domain.image_quality import (
    ConditionVerdict,
    QualityCondition,
    QualityFinding,
    QualityReport,
)
from tcg_ml_card_detection import CARD_DETECTION_VERSION
from tcg_ml_condition import CONDITION_VERSION
from tcg_ml_image_quality import IMAGE_QUALITY_VERSION
from tcg_ml_normalization import NORMALIZATION_VERSION, Normalized
from tcg_shared.storage.keys import StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def a_report(status: QualityStatus = QualityStatus.ACCEPTABLE, score: float = 0.8) -> QualityReport:
    """A whole report whose overall verdict is `status`.

    Built from a real `QualityReport` rather than a stand-in, so what gets
    written is the document the gate actually produces.
    """
    findings = []
    for condition in QualityCondition:
        if condition is QualityCondition.BLUR and status in (
            QualityStatus.POOR,
            QualityStatus.UNUSABLE,
        ):
            findings.append(
                QualityFinding(
                    condition=condition,
                    verdict=ConditionVerdict.DETECTED,
                    severity=status,
                    measurement=12.5,
                )
            )
        else:
            findings.append(
                QualityFinding(
                    condition=condition,
                    verdict=ConditionVerdict.UNDETERMINED,
                    reason="not checked in this fixture",
                )
            )
    return QualityReport(
        findings=tuple(findings),
        score=score,
        version="image-quality-heuristic-v0.2.0",
        thresholds={"blur_variance_poor": 120.0},
    )


#: Stands in for `quality.PIPELINE_VERSION`. A fixed string rather than the real
#: one, so a stage version bump does not silently rewrite what these tests mean.
A_PIPELINE = "image-quality-test-v0+card-detection-test-v0+normalization-test-v0"

#: Where an earlier run's artifact sits in the `wired` fixture's store. Named
#: for the column that holds it rather than for the storage key it is, because
#: gitleaks' `generic-api-key` rule fires on any identifier ending `_KEY` that
#: is assigned a string — and a suppression comment would be a worse trade than
#: the name the schema already uses.
CACHED_ARTIFACT_URI = "normalized/2026/08/22/earlier"


def a_cached_result(
    status: QualityStatus = QualityStatus.ACCEPTABLE,
    *,
    normalized_uri: str | None = CACHED_ARTIFACT_URI,
) -> CachedPipelineResult:
    """What an earlier run left on a row holding the same bytes."""
    report = a_report(status)
    artifact = an_artifact()
    return CachedPipelineResult(
        quality_score=report.score,
        quality_status=str(report.status),
        quality_details={**report.as_record(), "pipeline": A_PIPELINE},
        normalized_uri=normalized_uri,
        width=None if normalized_uri is None else artifact.width,
        height=None if normalized_uri is None else artifact.height,
        normalization_details=None if normalized_uri is None else artifact.as_record(),
    )


def a_geometry() -> CardGeometry:
    """A stand-in for what `tcg_ml_card_detection.detect` hands the gate."""
    return CardGeometry(
        corners=((10.0, 10.0), (90.0, 10.0), (90.0, 120.0), (10.0, 120.0)),
        confidence=Confidence.of(0.9),
        frame_width=100,
        frame_height=140,
        detector="card-detection-test-v0",
    )


def an_artifact() -> Normalized:
    """A stand-in for what `tcg_ml_normalization.normalize` hands the wiring."""
    return Normalized(
        data=b"\x89PNG\r\n\x1a\npretend",
        width=756,
        height=1056,
        matrix=(0.8, 0.0, -8.0, 0.0, 0.8, -8.0, 0.0, 0.0, 1.0),
        quarter_turns=0,
        version="normalization-test-v0",
        thresholds={"normalization_pixels_per_mm": 12.0},
    )


# ---------------------------------------------------------------------------
# The wiring — no database, no OpenCV, no network
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for the statements `prepare_images` issues."""

    def __init__(self, sides: dict[ImageSide, str]) -> None:
        self.sides = sides
        self.written: dict[ImageSide, QualityReport] = {}
        #: What each side's photograph was judged *with* — the card geometry the
        #: detector supplied, or whatever it handed back instead.
        self.judged: dict[str, object] = {}
        #: What was recorded for each side that got straightened. A side absent
        #: here is one whose `normalized_uri` stays NULL.
        self.normalized: dict[ImageSide, dict[str, Any]] = {}
        #: What an earlier run is pretending to have left for each side. Empty
        #: means every lookup misses, which is what most of these tests want.
        self.cached: dict[ImageSide, CachedPipelineResult] = {}
        #: The pipeline version each side was looked up under.
        self.looked_up: dict[ImageSide, str] = {}
        #: What was written onto each side that was served from cache.
        self.served: dict[ImageSide, dict[str, Any]] = {}
        #: The pipeline version each computed verdict was stamped with.
        self.recorded_pipeline: dict[ImageSide, str] = {}

    async def read_v1_image_keys(self, _db: Any, _analysis_id: UUID) -> dict[ImageSide, str]:
        return self.sides

    async def read_cached_pipeline_result(
        self, _db: Any, *, analysis_id: UUID, side: ImageSide, pipeline_version: str
    ) -> CachedPipelineResult | None:
        self.looked_up[side] = pipeline_version
        return self.cached.get(side)

    async def apply_cached_pipeline_result(
        self,
        _db: Any,
        *,
        analysis_id: UUID,
        side: ImageSide,
        cached: CachedPipelineResult,
        normalized_uri: str | None,
    ) -> None:
        self.served[side] = {"cached": cached, "normalized_uri": normalized_uri}

    async def record_quality(
        self,
        _db: Any,
        *,
        analysis_id: UUID,
        side: ImageSide,
        report: QualityReport,
        pipeline_version: str,
    ) -> None:
        self.written[side] = report
        self.recorded_pipeline[side] = pipeline_version

    async def record_normalization(
        self,
        _db: Any,
        *,
        analysis_id: UUID,
        side: ImageSide,
        normalized_uri: str,
        width: int,
        height: int,
        details: dict[str, Any],
    ) -> None:
        self.normalized[side] = {
            "normalized_uri": normalized_uri,
            "width": width,
            "height": height,
            "details": details,
        }


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Recorder, dict[str, QualityStatus]]]:
    """`prepare_images` with its store, its statements and its stages replaced."""
    storage = InMemoryObjectStorage()
    verdicts: dict[str, QualityStatus] = {}
    recorder = _Recorder({ImageSide.FRONT: "uploads/front", ImageSide.BACK: "uploads/back"})

    async def put() -> None:
        await storage.put(StorageKey("uploads/front"), b"front-bytes", content_type="image/jpeg")
        await storage.put(StorageKey("uploads/back"), b"back-bytes", content_type="image/jpeg")
        # The artifact an earlier run is pretending to have left, so that
        # `a_cached_result()` names an object that is actually there.
        await storage.put(
            StorageKey(CACHED_ARTIFACT_URI), b"an-earlier-artifact", content_type="image/png"
        )

    run(put)

    def assess(data: bytes, *, geometry: object = None, **_: object) -> QualityReport:
        side = "front" if data == b"front-bytes" else "back"
        recorder.judged[side] = geometry
        return a_report(verdicts.get(side, QualityStatus.ACCEPTABLE))

    monkeypatch.setattr(quality, "get_object_storage", lambda: storage)
    monkeypatch.setattr(quality, "read_v1_image_keys", recorder.read_v1_image_keys)
    monkeypatch.setattr(
        quality, "read_cached_pipeline_result", recorder.read_cached_pipeline_result
    )
    monkeypatch.setattr(
        quality, "apply_cached_pipeline_result", recorder.apply_cached_pipeline_result
    )
    monkeypatch.setattr(quality, "record_quality", recorder.record_quality)
    monkeypatch.setattr(quality, "record_normalization", recorder.record_normalization)
    monkeypatch.setattr(quality, "assess", assess)
    monkeypatch.setattr(quality, "detect", lambda _data: a_geometry())
    monkeypatch.setattr(quality, "normalize", lambda _data, _geometry: an_artifact())

    yield recorder, verdicts


def test_both_photographs_are_judged(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    recorder, _ = wired

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.written) == {ImageSide.FRONT, ImageSide.BACK}


def test_the_card_is_located_before_the_photograph_is_judged(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """Spec §18's fourth stage feeding spec §19's, which is issue #37's whole job.

    Without this the gate answers five of its eleven conditions `undetermined`
    forever, and no photograph can ever be `good`.
    """
    recorder, _ = wired

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.judged) == {"front", "back"}
    assert all(isinstance(geometry, CardGeometry) for geometry in recorder.judged.values())


def test_the_analysis_takes_the_worse_of_the_two(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """One unusable side is enough to stop the analysis — spec §19."""
    _, verdicts = wired
    verdicts["back"] = QualityStatus.UNUSABLE

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.UNUSABLE


def test_both_photographs_are_straightened(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """M2's acceptance criterion: an accepted upload yields a standardized artifact."""
    recorder, _ = wired

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.normalized) == {ImageSide.FRONT, ImageSide.BACK}
    assert recorder.normalized[ImageSide.FRONT]["width"] == 756
    assert recorder.normalized[ImageSide.FRONT]["height"] == 1056
    assert recorder.normalized[ImageSide.FRONT]["details"]["matrix"] == [
        0.8,
        0.0,
        -8.0,
        0.0,
        0.8,
        -8.0,
        0.0,
        0.0,
        1.0,
    ]


def test_the_artifact_is_stored_under_its_own_prefix(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """Never the upload's. An artifact and a photograph a user sent have
    different retention consequences, and only one of them is irreplaceable."""
    recorder, _ = wired

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    for written in recorder.normalized.values():
        assert written["normalized_uri"].startswith(f"{quality.NORMALIZED_NAMESPACE}/")


def test_a_photograph_with_no_locatable_card_is_not_straightened(
    monkeypatch: pytest.MonkeyPatch,
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """There is nothing to straighten, so `normalized_uri` stays NULL.

    The honest degradation, and the same one that leaves the gate's five
    geometric conditions `undetermined`. A whole frame resized to the target
    would be a standardized artifact of the table the card was lying on.
    """
    recorder, _ = wired
    monkeypatch.setattr(quality, "detect", lambda _data: INSUFFICIENT_INFORMATION)

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.written) == {ImageSide.FRONT, ImageSide.BACK}
    assert recorder.normalized == {}


def test_an_unusable_photograph_is_not_straightened(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """The analysis is about to fail, so the warp and the object are wasted."""
    recorder, verdicts = wired
    verdicts["back"] = QualityStatus.UNUSABLE

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.normalized) == {ImageSide.FRONT}


def test_a_stage_that_cannot_straighten_the_card_is_not_a_job_failure(
    monkeypatch: pytest.MonkeyPatch,
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """`normalize` answers rather than raising, and an answer of "could not" is
    a NULL column, not a dead-lettered analysis. The gate has already spoken."""
    recorder, _ = wired
    monkeypatch.setattr(quality, "normalize", lambda _data, _geometry: INSUFFICIENT_INFORMATION)

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.ACCEPTABLE
    assert recorder.normalized == {}


def test_a_photograph_that_cannot_be_fetched_is_not_quietly_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate that swallowed a storage failure would approve bytes it never saw."""
    recorder = _Recorder({ImageSide.FRONT: "uploads/missing"})
    monkeypatch.setattr(quality, "get_object_storage", InMemoryObjectStorage)
    monkeypatch.setattr(quality, "read_v1_image_keys", recorder.read_v1_image_keys)
    monkeypatch.setattr(
        quality, "read_cached_pipeline_result", recorder.read_cached_pipeline_result
    )
    monkeypatch.setattr(quality, "record_quality", recorder.record_quality)

    with pytest.raises(Exception, match="uploads/missing"):
        run(lambda: quality.prepare_images(object(), uuid.uuid4()))


def test_an_analysis_with_no_photographs_needs_no_object_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the client is what raises when storage is unconfigured, so
    hoisting it above the loop turns "nothing to judge" into a job failure about
    configuration — which is what it did, and what the migrations CI job (which
    has PostgreSQL and no MinIO) found."""
    recorder = _Recorder({})

    def refuse() -> InMemoryObjectStorage:
        raise RuntimeError("object storage is not configured")

    monkeypatch.setattr(quality, "get_object_storage", refuse)
    monkeypatch.setattr(quality, "read_v1_image_keys", recorder.read_v1_image_keys)
    monkeypatch.setattr(
        quality, "read_cached_pipeline_result", recorder.read_cached_pipeline_result
    )
    monkeypatch.setattr(quality, "record_quality", recorder.record_quality)

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.GOOD


# ---------------------------------------------------------------------------
# The cache — issue #39
# ---------------------------------------------------------------------------
def test_the_lookup_asks_for_the_running_pipeline_version(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """Every side is offered the cache, and under one identifier."""
    recorder, _ = wired

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert set(recorder.looked_up) == {ImageSide.FRONT, ImageSide.BACK}
    assert set(recorder.looked_up.values()) == {quality.PIPELINE_VERSION}


def test_the_pipeline_version_names_every_stage() -> None:
    """A stage bump that did not reach the key would serve its predecessor's work.

    The condition step joined the composition with #187: a condition version
    bump invalidates cached verdicts exactly as detection and quality bumps
    do. (The cache replays per-image rows, so the join buys invalidation
    consistency, not condition reuse — the step itself always runs.)
    """
    assert IMAGE_QUALITY_VERSION in quality.PIPELINE_VERSION
    assert CARD_DETECTION_VERSION in quality.PIPELINE_VERSION
    assert NORMALIZATION_VERSION in quality.PIPELINE_VERSION
    assert CONDITION_VERSION in quality.PIPELINE_VERSION


def test_a_photograph_already_processed_is_not_processed_again(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """The acceptance criterion: identical content is served, not recomputed."""
    recorder, _ = wired
    recorder.cached[ImageSide.FRONT] = a_cached_result()

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    # The front never reached the gate, and never reached `record_quality`
    # either — the cached row is written by the apply instead.
    assert "front" not in recorder.judged
    assert set(recorder.written) == {ImageSide.BACK}
    assert set(recorder.served) == {ImageSide.FRONT}
    assert "back" in recorder.judged


def test_a_served_verdict_counts_towards_the_analysis_verdict(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """A refusal is a cacheable result, and replaying it must still stop the run."""
    recorder, _ = wired
    recorder.cached[ImageSide.FRONT] = a_cached_result(QualityStatus.UNUSABLE)

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.UNUSABLE


def test_a_served_artifact_is_copied_to_a_key_of_its_own(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """Two rows must never name one object.

    `images.analysis_id` cascades and the retention sweep (#41) works from rows,
    so a shared artifact would be deleted out from under an analysis that still
    points at it.
    """
    recorder, _ = wired
    recorder.cached[ImageSide.FRONT] = a_cached_result()

    run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    storage = quality.get_object_storage()
    written = recorder.served[ImageSide.FRONT]["normalized_uri"]
    assert written.startswith(f"{quality.NORMALIZED_NAMESPACE}/")
    assert written != CACHED_ARTIFACT_URI
    assert run(lambda: storage.get(StorageKey(written))) == b"an-earlier-artifact"


def test_a_served_result_with_no_artifact_needs_no_object_store(
    monkeypatch: pytest.MonkeyPatch,
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """The unusable and no-card-located cases, and a pure win.

    There is nothing to copy, so the store is never built — which also keeps the
    property that an unconfigured store cannot turn "nothing to fetch" into a
    job failure about configuration.
    """
    recorder, _ = wired

    def refuse() -> InMemoryObjectStorage:
        raise RuntimeError("object storage is not configured")

    recorder.cached[ImageSide.FRONT] = a_cached_result(normalized_uri=None)
    recorder.cached[ImageSide.BACK] = a_cached_result(normalized_uri=None)
    monkeypatch.setattr(quality, "get_object_storage", refuse)

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.ACCEPTABLE
    assert set(recorder.served) == {ImageSide.FRONT, ImageSide.BACK}
    assert recorder.served[ImageSide.FRONT]["normalized_uri"] is None


def test_an_artifact_that_cannot_be_copied_is_a_miss_and_not_a_failure(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """A pointer at an object a retention sweep already took must never fail an
    analysis that can compute the answer itself."""
    recorder, _ = wired
    # Nothing is stored under this key, so the copy raises `ObjectNotFound`.
    recorder.cached[ImageSide.FRONT] = a_cached_result(
        normalized_uri="normalized/2026/08/01/swept-away"
    )

    verdict = run(lambda: quality.prepare_images(object(), uuid.uuid4()))

    assert verdict is QualityStatus.ACCEPTABLE
    assert recorder.served == {}
    assert set(recorder.written) == {ImageSide.FRONT, ImageSide.BACK}
    assert "front" in recorder.judged


# ---------------------------------------------------------------------------
# The write, against PostgreSQL
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
    """One analysis with one stored front, deleted afterwards."""
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


@pytest.fixture
def two_analyses() -> Iterator[tuple[UUID, UUID]]:
    """Two analyses under two sessions — a cache source and a cache target.

    The teardown deletes both sessions, which is the same cascade
    `test_a_cache_entry_expires_with_the_session_that_holds_it` asserts.
    """
    sessions = (uuid.uuid4(), uuid.uuid4())
    analyses = (uuid.uuid4(), uuid.uuid4())

    async def insert() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                for session_id, analysis_id in zip(sessions, analyses, strict=True):
                    await connection.execute(
                        sa.text(
                            "INSERT INTO analysis_sessions (id, anonymous_session_id, "
                            "expires_at, application_version) VALUES "
                            "(:id, :token, now() + interval '1 day', :v)"
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
                    sa.text("DELETE FROM analysis_sessions WHERE id = ANY(:ids)").bindparams(
                        ids=list(sessions)
                    )
                )
        finally:
            await engine.dispose()

    run(insert)
    try:
        yield analyses
    finally:
        run(delete)


def _store_side(db: Any, analysis_id: UUID, side: ImageSide, *, digest: str = "a" * 64) -> Any:
    return upsert_image(
        db,
        analysis_id=analysis_id,
        side=side,
        original_uri=f"uploads/2026/08/23/{analysis_id}-{side.value}",
        mime_type="image/jpeg",
        sha256=digest,
    )


async def _process(
    db: Any, analysis_id: UUID, side: ImageSide, *, pipeline: str, artifact: bool = True
) -> None:
    """What a completed run leaves on one image row."""
    await record_quality(
        db,
        analysis_id=analysis_id,
        side=side,
        report=a_report(),
        pipeline_version=pipeline,
    )
    if artifact:
        await record_normalization(
            db,
            analysis_id=analysis_id,
            side=side,
            normalized_uri=f"normalized/2026/08/23/{analysis_id}-{side.value}",
            width=756,
            height=1056,
            details=an_artifact().as_record(),
        )


def _expire(analysis_id: UUID):
    """Retention, reached the way retention reaches it: delete the session."""

    async def scenario(db: Any) -> None:
        await db.execute(
            sa.text(
                "DELETE FROM analysis_sessions WHERE id = "
                "(SELECT session_id FROM analyses WHERE id = :id)"
            ).bindparams(id=analysis_id)
        )

    return scenario


def _lookup(analysis_id: UUID, side: ImageSide = ImageSide.FRONT, *, pipeline: str = A_PIPELINE):
    async def scenario(db: Any) -> CachedPipelineResult | None:
        return await read_cached_pipeline_result(
            db, analysis_id=analysis_id, side=side, pipeline_version=pipeline
        )

    return scenario


def _store_front(db: Any, analysis_id: UUID) -> Any:
    return upsert_image(
        db,
        analysis_id=analysis_id,
        side=ImageSide.FRONT,
        original_uri="uploads/2026/08/21/front",
        mime_type="image/jpeg",
        sha256="a" * 64,
    )


@pytest.mark.integration
@requires_postgres
def test_a_verdict_round_trips_through_jsonb(analysis: UUID) -> None:
    report = a_report(QualityStatus.POOR, score=0.31)

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_quality(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            report=report,
            pipeline_version=A_PIPELINE,
        )

    run(lambda: _with_session(write))
    images = run(lambda: _with_session(lambda db: read_quality(db, analysis)))

    assert len(images) == 1
    assert images[0].quality_status == "poor"
    assert images[0].quality_score == pytest.approx(0.31)
    assert images[0].details == {**report.as_record(), "pipeline": A_PIPELINE}


@pytest.mark.integration
@requires_postgres
def test_the_status_written_is_the_one_the_findings_imply(analysis: UUID) -> None:
    """`record_quality` takes it from the report rather than from a parameter."""
    report = a_report(QualityStatus.UNUSABLE)

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_quality(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            report=report,
            pipeline_version=A_PIPELINE,
        )

    run(lambda: _with_session(write))
    images = run(lambda: _with_session(lambda db: read_quality(db, analysis)))

    assert images[0].quality_status == str(report.status) == "unusable"


@pytest.mark.integration
@requires_postgres
def test_a_score_outside_the_unit_interval_is_refused_by_the_database(analysis: UUID) -> None:
    """The CHECK the schema left for this issue to define (#31).

    Not decoration: `quality_score` is rendered as a fraction wherever it is
    shown, and a gate that produced 1.4 would produce a bar longer than its own
    track rather than an error.
    """

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await db.execute(
            sa.text("UPDATE images SET quality_score = 1.4 WHERE analysis_id = :id").bindparams(
                id=analysis
            )
        )

    with pytest.raises(IntegrityError, match="quality_score_is_a_unit_interval"):
        run(lambda: _with_session(write))


@pytest.mark.integration
@requires_postgres
def test_an_artifact_round_trips_through_jsonb(analysis: UUID) -> None:
    """Spec §51 has to reverse this mapping, so the nine numbers must survive."""
    artifact = an_artifact()

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_normalization(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            normalized_uri="normalized/2026/08/22/front",
            width=artifact.width,
            height=artifact.height,
            details=artifact.as_record(),
        )

    run(lambda: _with_session(write))
    row = run(lambda: _with_session(_read_normalization(analysis)))

    assert row.normalized_uri == "normalized/2026/08/22/front"
    assert (row.width, row.height) == (756, 1056)
    assert row.normalization_details == artifact.as_record()
    assert row.normalization_details["matrix"] == list(artifact.matrix)


@pytest.mark.integration
@requires_postgres
def test_a_retake_discards_everything_computed_from_the_photograph_it_replaced(
    analysis: UUID,
) -> None:
    """A verdict about bytes nobody kept is worse than no verdict at all, and an
    artifact made from them is worse still — a later stage would measure it."""

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_quality(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            report=a_report(QualityStatus.POOR),
            pipeline_version=A_PIPELINE,
        )
        await record_normalization(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            normalized_uri="normalized/2026/08/22/front",
            width=756,
            height=1056,
            details=an_artifact().as_record(),
        )

    async def retake(db: Any) -> None:
        await upsert_image(
            db,
            analysis_id=analysis,
            side=ImageSide.FRONT,
            original_uri="uploads/2026/08/21/front-again",
            mime_type="image/jpeg",
            sha256="b" * 64,
        )

    run(lambda: _with_session(write))
    held = run(lambda: _with_session(_read_objects(analysis)))
    run(lambda: _with_session(retake))
    images = run(lambda: _with_session(lambda db: read_quality(db, analysis)))
    row = run(lambda: _with_session(_read_normalization(analysis)))

    # Both objects have to be readable *before* the retake, or the upload
    # endpoint cannot delete the artifact it is about to unreference.
    assert set(held) == {"uploads/2026/08/21/front", "normalized/2026/08/22/front"}

    assert images[0].quality_status is None
    assert images[0].quality_score is None
    assert images[0].details is None
    assert row.normalized_uri is None
    assert (row.width, row.height) == (None, None)
    assert row.normalization_details is None


def _read_normalization(analysis_id: UUID) -> Callable[[Any], Awaitable[Any]]:
    async def read(db: Any) -> Any:
        result = await db.execute(
            sa.text(
                "SELECT normalized_uri, width, height, normalization_details "
                "FROM images WHERE analysis_id = :id"
            ).bindparams(id=analysis_id)
        )
        return result.one()

    return read


def _read_objects(analysis_id: UUID) -> Callable[[Any], Awaitable[tuple[str, ...]]]:
    async def read(db: Any) -> tuple[str, ...]:
        return await read_image_objects(db, analysis_id, ImageSide.FRONT)

    return read


# ---------------------------------------------------------------------------
# The cache lookup, against PostgreSQL — issue #39
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_row_processed_from_the_same_bytes_is_a_cache_entry(
    two_analyses: tuple[UUID, UUID],
) -> None:
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)
        await _store_side(db, target, ImageSide.FRONT)

    run(lambda: _with_session(write))
    found = run(lambda: _with_session(_lookup(target)))

    assert found is not None
    assert found.quality_status == "acceptable"
    assert found.quality_details["pipeline"] == A_PIPELINE
    assert found.normalized_uri == f"normalized/2026/08/23/{source}-front"
    assert found.width == 756


@pytest.mark.integration
@requires_postgres
def test_a_row_processed_by_another_pipeline_is_not_a_cache_entry(
    two_analyses: tuple[UUID, UUID],
) -> None:
    """The acceptance criterion: a pipeline change cannot serve stale artifacts."""
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline="something-older-v0")
        await _store_side(db, target, ImageSide.FRONT)

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(_lookup(target))) is None


@pytest.mark.integration
@requires_postgres
def test_a_row_holding_other_bytes_is_not_a_cache_entry(
    two_analyses: tuple[UUID, UUID],
) -> None:
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT, digest="a" * 64)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)
        await _store_side(db, target, ImageSide.FRONT, digest="b" * 64)

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(_lookup(target))) is None


@pytest.mark.integration
@requires_postgres
def test_a_row_is_not_its_own_cache_entry(two_analyses: tuple[UUID, UUID]) -> None:
    """`src.id <> target.id` excludes the row itself and nothing else.

    Without it a second run over an analysis that had already been processed
    would serve that analysis its own artifacts and skip the work — harmless
    today, and a trap the moment anything reprocesses.
    """
    source, _ = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(_lookup(source))) is None


@pytest.mark.integration
@requires_postgres
def test_the_other_side_of_the_same_analysis_is_a_cache_entry(
    two_analyses: tuple[UUID, UUID],
) -> None:
    """And it is a live path, not a curiosity.

    Sides are processed in sorted order and `ImageSide` is a `StrEnum`, so
    `back` runs before `front`; a user who uploads one file twice has the front
    served from the back inside the same uncommitted transaction.
    """
    source, _ = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.BACK)
        await _process(db, source, ImageSide.BACK, pipeline=A_PIPELINE)
        await _store_side(db, source, ImageSide.FRONT)

    run(lambda: _with_session(write))
    found = run(lambda: _with_session(_lookup(source)))

    assert found is not None
    assert found.normalized_uri == f"normalized/2026/08/23/{source}-back"


@pytest.mark.integration
@requires_postgres
def test_a_row_with_a_pipeline_but_no_verdict_is_not_served(
    two_analyses: tuple[UUID, UUID],
) -> None:
    """Belt and braces against a row that claims a version it did not finish.

    Serving one would put NULL where the fold and the log expect a status, so
    the predicate refuses it however the row came to exist.
    """
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)
        await db.execute(
            sa.text(
                "UPDATE images SET quality_status = NULL, quality_score = NULL "
                "WHERE analysis_id = :id"
            ).bindparams(id=source)
        )
        await _store_side(db, target, ImageSide.FRONT)

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(_lookup(target))) is None


@pytest.mark.integration
@requires_postgres
def test_a_cache_entry_expires_with_the_session_that_holds_it(
    two_analyses: tuple[UUID, UUID],
) -> None:
    """Spec §54, and the reason the cache is this table rather than a store of
    its own: there is no second expiry policy to keep in sync."""
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)
        await _store_side(db, target, ImageSide.FRONT)

    run(lambda: _with_session(write))
    assert run(lambda: _with_session(_lookup(target))) is not None

    run(lambda: _with_session(_expire(source)))

    assert run(lambda: _with_session(_lookup(target))) is None


@pytest.mark.integration
@requires_postgres
def test_a_served_result_lands_on_the_target_row(two_analyses: tuple[UUID, UUID]) -> None:
    """Every derived column moves across, and `normalized_uri` is the new key."""
    source, target = two_analyses

    async def write(db: Any) -> None:
        await _store_side(db, source, ImageSide.FRONT)
        await _process(db, source, ImageSide.FRONT, pipeline=A_PIPELINE)
        await _store_side(db, target, ImageSide.FRONT)

    async def serve(db: Any) -> None:
        cached = await read_cached_pipeline_result(
            db, analysis_id=target, side=ImageSide.FRONT, pipeline_version=A_PIPELINE
        )
        assert cached is not None
        await apply_cached_pipeline_result(
            db,
            analysis_id=target,
            side=ImageSide.FRONT,
            cached=cached,
            normalized_uri="normalized/2026/08/23/a-copy",
        )

    run(lambda: _with_session(write))
    run(lambda: _with_session(serve))
    images = run(lambda: _with_session(lambda db: read_quality(db, target)))
    row = run(lambda: _with_session(_read_normalization(target)))

    assert images[0].quality_status == "acceptable"
    assert images[0].details is not None
    assert images[0].details["pipeline"] == A_PIPELINE
    assert row.normalized_uri == "normalized/2026/08/23/a-copy"
    assert (row.width, row.height) == (756, 1056)
    assert row.normalization_details == an_artifact().as_record()
