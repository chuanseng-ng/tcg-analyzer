"""The stall sweep against real PostgreSQL — issue #272.

Deliberately not a fake, for `test_analysis_state.py`'s reason. What the sweep
has to get right is which rows a `HAVING max(images.created_at) < now() -
interval` selects and what `state.transition` then refuses, and both are
properties of statements PostgreSQL executes — including the clock, which is the
database's and never this process's.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.stalls import STALLED_AFTER_SECONDS, sweep_stalled
from tcg_api.analysis.tables import analyses, analysis_sessions, images
from tcg_api.database import create_session_factory
from tcg_domain.analysis import AnalysisStatus, ImageSide

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to sweep",
    ),
]

JPEG = "image/jpeg"
DIGEST = "c" * 64

#: Comfortably past the threshold, and comfortably short of it. Both are offsets
#: from *this* process's clock, which is the honest way to test a comparison the
#: database makes against its own: a skew large enough to break these is a skew
#: worth failing on.
STALE = timedelta(seconds=STALLED_AFTER_SECONDS + 60)
FRESH = timedelta(seconds=30)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base` and pytest orders nothing."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    def truncate() -> None:
        async def scenario() -> None:
            engine = create_async_engine(DATABASE_URL or "")
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.text("TRUNCATE analysis_sessions RESTART IDENTITY CASCADE")
                    )
            finally:
                await engine.dispose()

        run(scenario)

    truncate()
    yield
    truncate()


def seed(*, status: AnalysisStatus, photographed: timedelta | None) -> uuid.UUID:
    """Write one session and one analysis in `status`. Returns the analysis id.

    `photographed` is how long ago its two photographs arrived, or `None` for an
    analysis that has none — the state a session sits in between `POST
    /analyses` and the first upload, which no sweep should touch.
    """
    session_id, analysis_id = uuid.uuid4(), uuid.uuid4()
    arrived = None if photographed is None else datetime.now(UTC) - photographed

    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.insert(analysis_sessions),
                    {
                        "id": session_id,
                        "anonymous_session_id": uuid.uuid4().hex,
                        "application_version": "0.1.0",
                        "expires_at": datetime.now(UTC) + timedelta(days=7),
                    },
                )
                await connection.execute(
                    sa.insert(analyses),
                    {
                        "id": analysis_id,
                        "session_id": session_id,
                        "status": status.value,
                        # #265's CHECK is strict both ways, so a row seeded
                        # `failed` carries a reason like any row the runner
                        # writes. Any reason but `stalled`, so the assertion
                        # that this row was left alone means something.
                        **(
                            {"failure_code": "analysis_failed", "failure_reason": "model_failed"}
                            if status is AnalysisStatus.FAILED
                            else {}
                        ),
                    },
                )
                if arrived is not None:
                    await connection.execute(
                        sa.insert(images),
                        [
                            {
                                "id": uuid.uuid4(),
                                "analysis_id": analysis_id,
                                "side": side.value,
                                "original_uri": f"originals/{uuid.uuid4().hex}",
                                "mime_type": JPEG,
                                "sha256": DIGEST,
                                "created_at": arrived,
                            }
                            for side in ImageSide
                        ],
                    )
        finally:
            await engine.dispose()

    run(scenario)
    return analysis_id


def sweep(*, limit: int = 100) -> int:
    async def scenario() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with create_session_factory(engine)() as db:
                return await sweep_stalled(db, limit=limit)
        finally:
            await engine.dispose()

    return run(scenario)


def read(analysis_id: uuid.UUID) -> sa.Row[tuple[str, str | None, str | None, datetime | None]]:
    async def scenario() -> sa.Row[tuple[str, str | None, str | None, datetime | None]]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                result = await connection.execute(
                    sa.select(
                        analyses.c.status,
                        analyses.c.failure_code,
                        analyses.c.failure_reason,
                        analyses.c.completed_at,
                    ).where(analyses.c.id == analysis_id)
                )
                return result.one()
        finally:
            await engine.dispose()

    return run(scenario)


def test_an_analysis_nobody_ran_is_failed_with_its_reason() -> None:
    """The row a hard-killed run leaves behind, and the whole point of the sweep.

    A hard limit acks the message and rolls the run's one transaction back, so
    the analysis is `uploaded` again with nothing written and no exception
    anywhere. Fifteen minutes later this is what says so — through
    `state.transition`, so the §66 code, the reason and `completed_at` land in
    the one statement that is allowed to write them (#265).
    """
    analysis_id = seed(status=AnalysisStatus.UPLOADED, photographed=STALE)

    assert sweep() == 1

    row = read(analysis_id)
    assert row.status == AnalysisStatus.FAILED.value
    assert row.failure_reason == "stalled"
    assert row.failure_code == "analysis_failed"
    assert row.completed_at is not None


def test_an_analysis_still_within_the_window_is_left_alone() -> None:
    """A run this sweep would be racing is a run it must not touch."""
    analysis_id = seed(status=AnalysisStatus.UPLOADED, photographed=FRESH)

    assert sweep() == 0
    assert read(analysis_id).status == AnalysisStatus.UPLOADED.value


@pytest.mark.parametrize(
    "status",
    [
        AnalysisStatus.AWAITING_CONFIRMATION,
        AnalysisStatus.ANALYZING,
        AnalysisStatus.COMPLETED,
        AnalysisStatus.FAILED,
    ],
    ids=lambda status: status.value,
)
def test_only_uploaded_is_swept(status: AnalysisStatus) -> None:
    """`awaiting_confirmation` and `analyzing` are the *user's* to sit in.

    An analysis waiting at the confirmation gate has been there since the run
    finished and may be there for days — spec §65 hands it back deliberately.
    Sweeping by age alone would fail the analyses that worked.
    """
    analysis_id = seed(status=status, photographed=STALE)

    assert sweep() == 0
    assert read(analysis_id).status == status.value


def test_an_analysis_with_no_photographs_is_not_stalled() -> None:
    """Nothing has been asked of it: `POST /analyses` ran and the upload did not.

    It expires on the retention sweep's schedule like any other abandoned
    session (#41), and a `failed` row would claim a run that never began.
    """
    analysis_id = seed(status=AnalysisStatus.UPLOADED, photographed=None)

    assert sweep() == 0
    assert read(analysis_id).status == AnalysisStatus.UPLOADED.value


def test_the_batch_is_bounded_and_takes_the_oldest_first() -> None:
    """A backlog is swept over several ticks, oldest photograph first.

    The bound is the retention sweep's argument: a tick that tries to move every
    stalled row is a tick that holds locks proportional to a backlog nobody has
    seen yet.
    """
    older = seed(status=AnalysisStatus.UPLOADED, photographed=STALE + timedelta(hours=1))
    newer = seed(status=AnalysisStatus.UPLOADED, photographed=STALE)

    assert sweep(limit=1) == 1
    assert read(older).status == AnalysisStatus.FAILED.value
    assert read(newer).status == AnalysisStatus.UPLOADED.value

    assert sweep(limit=1) == 1
    assert read(newer).status == AnalysisStatus.FAILED.value
