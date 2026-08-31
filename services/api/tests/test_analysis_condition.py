"""Running and recording the condition step — issue #187, spec §13, §2.7.

Two halves, `test_analysis_quality.py`'s split. The wiring half needs nothing:
`assess_condition` reads two stored artifacts, derives each side's card frame
from its **stored** record, hands both to the composer and writes one document
— stubbed stages and `InMemoryObjectStorage` are a truthful stand-in for all
of it. The persistence half needs real PostgreSQL, because what
`record_condition` and `read_v1_artifacts` have to get right is that a JSONB
document round-trips and that a side nothing straightened comes back as an
honest pair of NULLs.

The one rule under test everywhere: **a step that runs always writes a
document** — the assessment's record, or a top-level
`insufficient_information` with its reason. NULL stays reserved for "the step
never ran".
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
from tcg_api.analysis import condition
from tcg_api.analysis.images import StoredArtifact, read_v1_artifacts, upsert_image
from tcg_api.analysis.sessions import record_condition
from tcg_api.analysis.tables import analyses
from tcg_api.database import create_session_factory
from tcg_api.version import application_version
from tcg_domain.analysis import ImageSide
from tcg_domain.annotation import CornerLabel, CornerRegion, EdgeLabel, EdgeRegion
from tcg_domain.condition import (
    BoundingBox,
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
from tcg_ml_centering import DEFAULT_CENTERING_THRESHOLDS
from tcg_ml_condition import CONDITION_VERSION
from tcg_ml_corners import DEFAULT_CORNER_THRESHOLDS
from tcg_ml_edges import DEFAULT_EDGE_THRESHOLDS
from tcg_ml_surface import DEFAULT_SURFACE_THRESHOLDS
from tcg_shared.storage.keys import StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


#: What `record_normalization` left for a #194-era artifact: 804 by 1104 with
#: the card behind a 24 px margin (2 mm at 12 px/mm).
def a_normalization_record() -> dict[str, Any]:
    return {
        "version": "normalization-test-v0",
        "width": 804,
        "height": 1104,
        "quarter_turns": 0,
        "matrix": [0.8, 0.0, -8.0, 0.0, 0.8, -8.0, 0.0, 0.0, 1.0],
        "thresholds": {
            "normalization_margin_mm": 2.0,
            "normalization_pixels_per_mm": 12.0,
        },
    }


#: The frame `a_normalization_record()` derives to.
A_CARD_FRAME = BoundingBox(x=24 / 804, y=24 / 1104, width=756 / 804, height=1056 / 1104)


def an_assessment() -> ConditionAssessment:
    """A minimal real assessment, so what gets written is a real record."""
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


#: Every analyzer's defaults, merged — prefixed, so the merge cannot collide.
MERGED_THRESHOLDS = {
    **DEFAULT_CENTERING_THRESHOLDS.as_record(),
    **DEFAULT_CORNER_THRESHOLDS.as_record(),
    **DEFAULT_EDGE_THRESHOLDS.as_record(),
    **DEFAULT_SURFACE_THRESHOLDS.as_record(),
}


# ---------------------------------------------------------------------------
# The wiring — no database, no OpenCV, no network
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for the statements `assess_condition` issues."""

    def __init__(self, rows: dict[ImageSide, StoredArtifact]) -> None:
        self.rows = rows
        self.documents: list[dict[str, Any]] = []

    async def read_v1_artifacts(
        self, _db: Any, _analysis_id: UUID
    ) -> dict[ImageSide, StoredArtifact]:
        return self.rows

    async def record_condition(
        self, _db: Any, _analysis_id: UUID, *, details: dict[str, Any]
    ) -> None:
        self.documents.append(details)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Recorder, list[dict[str, Any]]]]:
    """`assess_condition` with its store, its statements and the composer replaced."""
    storage = InMemoryObjectStorage()
    recorder = _Recorder(
        {
            ImageSide.FRONT: StoredArtifact(
                normalized_uri="normalized/2026/08/31/front",
                normalization_details=a_normalization_record(),
            ),
            ImageSide.BACK: StoredArtifact(
                normalized_uri="normalized/2026/08/31/back",
                normalization_details=a_normalization_record(),
            ),
        }
    )
    calls: list[dict[str, Any]] = []

    async def put() -> None:
        await storage.put(
            StorageKey("normalized/2026/08/31/front"), b"front-artifact", content_type="image/png"
        )
        await storage.put(
            StorageKey("normalized/2026/08/31/back"), b"back-artifact", content_type="image/png"
        )

    run(put)

    def assess_artifacts(
        front: bytes,
        back: bytes,
        *,
        front_card_frame: BoundingBox,
        back_card_frame: BoundingBox,
    ) -> Any:
        calls.append(
            {
                "front": front,
                "back": back,
                "front_card_frame": front_card_frame,
                "back_card_frame": back_card_frame,
            }
        )
        return an_assessment()

    monkeypatch.setattr(condition, "get_object_storage", lambda: storage)
    monkeypatch.setattr(condition, "read_v1_artifacts", recorder.read_v1_artifacts)
    monkeypatch.setattr(condition, "record_condition", recorder.record_condition)
    monkeypatch.setattr(condition, "assess_artifacts", assess_artifacts)

    yield recorder, calls


def test_both_artifacts_feed_the_composer_with_their_stored_frames(
    wired: tuple[_Recorder, list[dict[str, Any]]],
) -> None:
    """The bytes are the stored artifacts and each frame is derived from that
    side's own `normalization_details` — #182's rule, never the normalizer's
    current thresholds."""
    recorder, calls = wired

    run(lambda: condition.assess_condition(None, uuid.uuid4()))

    assert calls == [
        {
            "front": b"front-artifact",
            "back": b"back-artifact",
            "front_card_frame": A_CARD_FRAME,
            "back_card_frame": A_CARD_FRAME,
        }
    ]
    assert len(recorder.documents) == 1


def test_the_document_is_the_record_beside_the_version_and_the_thresholds(
    wired: tuple[_Recorder, list[dict[str, Any]]],
) -> None:
    """#186's rule: an assessment carries no version and no thresholds, so the
    caller records `CONDITION_VERSION` and the four analyzers' `as_record()`s
    beside the document — a row explains itself."""
    recorder, _ = wired

    run(lambda: condition.assess_condition(None, uuid.uuid4()))

    document = recorder.documents[0]
    assert tuple(document) == ("version", "thresholds", "assessment")
    assert document["version"] == CONDITION_VERSION
    assert document["thresholds"] == MERGED_THRESHOLDS
    assert document["assessment"] == an_assessment().as_record()


def test_a_side_with_no_artifact_is_recorded_as_the_refusal(
    wired: tuple[_Recorder, list[dict[str, Any]]],
) -> None:
    """No card was located, so there is nothing to assess — and the step still
    writes a document, because NULL must keep meaning "the step never ran"."""
    recorder, calls = wired
    recorder.rows[ImageSide.FRONT] = StoredArtifact(normalized_uri=None, normalization_details=None)

    run(lambda: condition.assess_condition(None, uuid.uuid4()))

    assert calls == []
    assert recorder.documents == [
        {
            "version": CONDITION_VERSION,
            "thresholds": MERGED_THRESHOLDS,
            "insufficient_information": "no_normalized_artifact_for_front",
        }
    ]


def test_a_side_with_no_derivable_frame_is_recorded_as_the_refusal(
    wired: tuple[_Recorder, list[dict[str, Any]]],
) -> None:
    """An artifact whose record derives no frame cannot be measured against —
    a refusal wearing its reason, never a guessed unit square."""
    recorder, calls = wired
    recorder.rows[ImageSide.BACK] = StoredArtifact(
        normalized_uri="normalized/2026/08/31/back", normalization_details=None
    )

    run(lambda: condition.assess_condition(None, uuid.uuid4()))

    assert calls == []
    assert recorder.documents[0]["insufficient_information"] == "no_card_frame_for_back"


def test_the_composers_own_refusal_is_stored_as_itself(
    wired: tuple[_Recorder, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`no_axis_measured` is a legitimate result (spec §2.7) and is persisted
    as exactly what it is — never dropped, never zero."""
    recorder, _ = wired
    monkeypatch.setattr(
        condition,
        "assess_artifacts",
        lambda *_args, **_kwargs: InsufficientInformation("no_axis_measured"),
    )

    run(lambda: condition.assess_condition(None, uuid.uuid4()))

    document = recorder.documents[0]
    assert document["insufficient_information"] == "no_axis_measured"
    assert "assessment" not in document


def test_the_outcome_is_logged_with_the_analysis_it_belongs_to(
    wired: tuple[_Recorder, list[dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    """The identifier has to actually reach the rendered line — the structlog
    keyword rule; a stdlib `extra` mapping is silently dropped by
    `configure_logging`'s formatter chain."""
    from tcg_api.config import Settings
    from tcg_api.logging import configure_logging

    configure_logging(Settings(log_format="json"))
    analysis_id = uuid.uuid4()

    run(lambda: condition.assess_condition(None, analysis_id))

    written = capsys.readouterr()
    line = next(
        entry
        for entry in (written.out + written.err).splitlines()
        if "analysis.condition_assessed" in entry
    )
    assert str(analysis_id) in line
    assert CONDITION_VERSION in line


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
def test_the_condition_document_round_trips_through_jsonb(analysis: UUID) -> None:
    document = {
        "version": CONDITION_VERSION,
        "thresholds": MERGED_THRESHOLDS,
        "assessment": an_assessment().as_record(),
    }

    async def write(db: Any) -> None:
        await record_condition(db, analysis, details=document)

    async def read(db: Any) -> Any:
        result = await db.execute(
            sa.select(analyses.c.condition_details).where(analyses.c.id == analysis)
        )
        return result.scalar_one()

    run(lambda: _with_session(write))

    assert run(lambda: _with_session(read)) == document


@pytest.mark.integration
@requires_postgres
def test_a_side_nothing_straightened_reads_back_as_an_honest_pair_of_nulls(
    analysis: UUID,
) -> None:
    """`read_v1_artifacts` is `read_v1_image_keys`' sibling: both V1 sides,
    None-tolerant, because a photograph no card was located in has a row and
    no artifact."""

    async def arrange(db: Any) -> None:
        for side in (ImageSide.FRONT, ImageSide.BACK):
            await upsert_image(
                db,
                analysis_id=analysis,
                side=side,
                original_uri=f"uploads/2026/08/31/{analysis}-{side.value}",
                mime_type="image/jpeg",
                sha256=("a" if side is ImageSide.FRONT else "b") * 64,
            )
        await db.execute(
            sa.text(
                "UPDATE images SET normalized_uri = :uri, normalization_details = "
                "CAST(:details AS jsonb) WHERE analysis_id = :analysis_id AND side = 'front'"
            ).bindparams(
                uri="normalized/2026/08/31/front",
                details='{"width": 804, "height": 1104}',
                analysis_id=analysis,
            )
        )

    async def read(db: Any) -> dict[ImageSide, StoredArtifact]:
        return await read_v1_artifacts(db, analysis)

    run(lambda: _with_session(arrange))
    rows = run(lambda: _with_session(read))

    assert rows[ImageSide.FRONT] == StoredArtifact(
        normalized_uri="normalized/2026/08/31/front",
        normalization_details={"width": 804, "height": 1104},
    )
    assert rows[ImageSide.BACK] == StoredArtifact(normalized_uri=None, normalization_details=None)
