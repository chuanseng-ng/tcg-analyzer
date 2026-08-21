"""Recording the quality gate's verdict — issue #36, spec §19.

Two halves, split by what each actually needs.

The first needs nothing: `assess_analysis` reads bytes from a port and folds two
verdicts into one, and `InMemoryObjectStorage` plus a stubbed gate is a truthful
stand-in for both. What is asserted there is the fold and the fact that the
photographs are read from storage rather than from anywhere else.

The second needs real PostgreSQL, because what `record_quality` has to get right
is that a JSONB document round-trips and that the `[0, 1]` CHECK the migration
added actually refuses a bad score. A fake that answered those in Python would
be testing the fake — the argument `test_analysis_state.py` makes at length.
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
from tcg_api.analysis.images import read_quality, record_quality, upsert_image
from tcg_api.database import create_session_factory
from tcg_api.version import application_version
from tcg_domain.analysis import ImageSide, QualityStatus
from tcg_domain.image_quality import (
    ConditionVerdict,
    QualityCondition,
    QualityFinding,
    QualityReport,
)
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
        version="image-quality-heuristic-v0.1.0",
        thresholds={"blur_variance_poor": 120.0},
    )


# ---------------------------------------------------------------------------
# The wiring — no database, no OpenCV, no network
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for the two statements `assess_analysis` issues."""

    def __init__(self, sides: dict[ImageSide, str]) -> None:
        self.sides = sides
        self.written: dict[ImageSide, QualityReport] = {}

    async def read_v1_image_keys(self, _db: Any, _analysis_id: UUID) -> dict[ImageSide, str]:
        return self.sides

    async def record_quality(
        self, _db: Any, *, analysis_id: UUID, side: ImageSide, report: QualityReport
    ) -> None:
        self.written[side] = report


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Recorder, dict[str, QualityStatus]]]:
    """`assess_analysis` with its store, its statements and its gate replaced."""
    storage = InMemoryObjectStorage()
    verdicts: dict[str, QualityStatus] = {}
    recorder = _Recorder({ImageSide.FRONT: "uploads/front", ImageSide.BACK: "uploads/back"})

    async def put() -> None:
        await storage.put(StorageKey("uploads/front"), b"front-bytes", content_type="image/jpeg")
        await storage.put(StorageKey("uploads/back"), b"back-bytes", content_type="image/jpeg")

    run(put)

    def assess(data: bytes, **_: object) -> QualityReport:
        side = "front" if data == b"front-bytes" else "back"
        return a_report(verdicts.get(side, QualityStatus.ACCEPTABLE))

    monkeypatch.setattr(quality, "get_object_storage", lambda: storage)
    monkeypatch.setattr(quality, "read_v1_image_keys", recorder.read_v1_image_keys)
    monkeypatch.setattr(quality, "record_quality", recorder.record_quality)
    monkeypatch.setattr(quality, "assess", assess)

    yield recorder, verdicts


def test_both_photographs_are_judged(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    recorder, _ = wired

    run(lambda: quality.assess_analysis(object(), uuid.uuid4()))

    assert set(recorder.written) == {ImageSide.FRONT, ImageSide.BACK}


def test_the_analysis_takes_the_worse_of_the_two(
    wired: tuple[_Recorder, dict[str, QualityStatus]],
) -> None:
    """One unusable side is enough to stop the analysis — spec §19."""
    _, verdicts = wired
    verdicts["back"] = QualityStatus.UNUSABLE

    verdict = run(lambda: quality.assess_analysis(object(), uuid.uuid4()))

    assert verdict is QualityStatus.UNUSABLE


def test_a_photograph_that_cannot_be_fetched_is_not_quietly_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate that swallowed a storage failure would approve bytes it never saw."""
    recorder = _Recorder({ImageSide.FRONT: "uploads/missing"})
    monkeypatch.setattr(quality, "get_object_storage", InMemoryObjectStorage)
    monkeypatch.setattr(quality, "read_v1_image_keys", recorder.read_v1_image_keys)
    monkeypatch.setattr(quality, "record_quality", recorder.record_quality)

    with pytest.raises(Exception, match="uploads/missing"):
        run(lambda: quality.assess_analysis(object(), uuid.uuid4()))


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
        await record_quality(db, analysis_id=analysis, side=ImageSide.FRONT, report=report)

    run(lambda: _with_session(write))
    images = run(lambda: _with_session(lambda db: read_quality(db, analysis)))

    assert len(images) == 1
    assert images[0].quality_status == "poor"
    assert images[0].quality_score == pytest.approx(0.31)
    assert images[0].details == report.as_record()


@pytest.mark.integration
@requires_postgres
def test_the_status_written_is_the_one_the_findings_imply(analysis: UUID) -> None:
    """`record_quality` takes it from the report rather than from a parameter."""
    report = a_report(QualityStatus.UNUSABLE)

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_quality(db, analysis_id=analysis, side=ImageSide.FRONT, report=report)

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
def test_a_retake_discards_the_verdict_about_the_photograph_it_replaced(analysis: UUID) -> None:
    """A verdict about bytes nobody kept is worse than no verdict at all."""

    async def write(db: Any) -> None:
        await _store_front(db, analysis)
        await record_quality(
            db, analysis_id=analysis, side=ImageSide.FRONT, report=a_report(QualityStatus.POOR)
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
    run(lambda: _with_session(retake))
    images = run(lambda: _with_session(lambda db: read_quality(db, analysis)))

    assert images[0].quality_status is None
    assert images[0].quality_score is None
    assert images[0].details is None
