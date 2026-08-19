"""`state.transition` against real PostgreSQL — issue #35.

This module deliberately does not use a fake. What `transition` has to get right
is that a *conditional* `UPDATE` is what refuses an illegal move, stops a
duplicate job delivery and settles a race between two workers — and all three of
those are properties of the statement PostgreSQL executes. A stub that answered
them in Python would be testing the stub, which is the same argument
`test_analyses_endpoint.py` makes about ownership living in a `WHERE` clause.

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
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.sessions import AnalysisStoreUnavailable
from tcg_api.analysis.state import transition
from tcg_api.database import create_session_factory
from tcg_api.version import application_version
from tcg_domain.analysis import AnalysisStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

pytestmark = [pytest.mark.integration, requires_postgres]


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


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
    """One analysis in one session, in `created`, deleted afterwards.

    Written straight through SQLAlchemy rather than through `POST /analyses`,
    because these tests are about the statement and not about the endpoint.
    """
    session_id, analysis_id = uuid.uuid4(), uuid.uuid4()

    async def insert() -> None:
        await _execute_all(
            sa.text(
                "INSERT INTO analysis_sessions (id, anonymous_session_id, expires_at, "
                "application_version) VALUES (:id, :token, now() + interval '1 day', :version)"
            ).bindparams(id=session_id, token=str(session_id), version=application_version()),
            sa.text("INSERT INTO analyses (id, session_id) VALUES (:id, :session_id)").bindparams(
                id=analysis_id, session_id=session_id
            ),
        )

    async def delete() -> None:
        await _execute_all(
            sa.text("DELETE FROM analysis_sessions WHERE id = :id").bindparams(id=session_id)
        )

    run(insert)
    try:
        yield analysis_id
    finally:
        run(delete)


async def _execute_all(*statements: Any) -> None:
    engine = create_async_engine(DATABASE_URL or "")
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(statement)
    finally:
        await engine.dispose()


def moving(analysis_id: UUID, *targets: AnalysisStatus) -> list[bool]:
    """Apply each target in turn through one session, committing as it goes."""

    async def scenario() -> list[bool]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with create_session_factory(engine)() as db:
                moved = [await transition(db, analysis_id, to=target) for target in targets]
                await db.commit()
                return moved
        finally:
            await engine.dispose()

    return run(scenario)


def state_of(analysis_id: UUID) -> tuple[str, Any]:
    async def read() -> tuple[str, Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        sa.text("SELECT status, completed_at FROM analyses WHERE id = :id"),
                        {"id": analysis_id},
                    )
                ).one()
                return row.status, row.completed_at
        finally:
            await engine.dispose()

    return run(read)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_an_analysis_walks_the_pipeline(analysis: UUID) -> None:
    """Spec §65's states, in §65's order, one legal step at a time."""
    moved = moving(
        analysis,
        AnalysisStatus.UPLOADING,
        AnalysisStatus.UPLOADED,
        AnalysisStatus.IDENTIFYING,
        AnalysisStatus.AWAITING_CONFIRMATION,
    )

    assert moved == [True, True, True, True]
    assert state_of(analysis) == ("awaiting_confirmation", None)


# ---------------------------------------------------------------------------
# What the `WHERE` clause refuses
# ---------------------------------------------------------------------------


def test_an_illegal_move_changes_nothing(analysis: UUID) -> None:
    """Skipping the pipeline is not a smaller version of running it."""
    assert moving(analysis, AnalysisStatus.COMPLETED) == [False]
    assert state_of(analysis)[0] == "created"


def test_an_analysis_cannot_be_rewound(analysis: UUID) -> None:
    moving(analysis, AnalysisStatus.UPLOADING, AnalysisStatus.UPLOADED)

    assert moving(analysis, AnalysisStatus.UPLOADING) == [False]
    assert state_of(analysis)[0] == "uploaded"


def test_a_terminal_analysis_is_not_moved_again(analysis: UUID) -> None:
    moving(analysis, AnalysisStatus.FAILED)

    assert moving(analysis, AnalysisStatus.UPLOADING) == [False]
    assert moving(analysis, AnalysisStatus.COMPLETED) == [False]
    assert state_of(analysis)[0] == "failed"


def test_nothing_moves_to_created(analysis: UUID) -> None:
    """It is where a row starts. `legal_predecessors` is empty, so there is no `IN`."""
    moving(analysis, AnalysisStatus.UPLOADING)

    assert moving(analysis, AnalysisStatus.CREATED) == [False]


def test_an_analysis_that_does_not_exist_is_simply_not_moved() -> None:
    """`False` rather than a raise: a job for a purged analysis has nothing to do."""
    assert moving(uuid.uuid4(), AnalysisStatus.UPLOADING) == [False]


# ---------------------------------------------------------------------------
# Idempotency — the reason the check is in the statement
# ---------------------------------------------------------------------------


def test_the_second_identical_claim_loses(analysis: UUID) -> None:
    """At-least-once delivery is safe precisely because of this answer.

    The two calls are the two deliveries of one job. Only one of them may report
    having moved the analysis, and the other must not raise — it simply has
    nothing to do.
    """
    moving(analysis, AnalysisStatus.UPLOADING, AnalysisStatus.UPLOADED)

    assert moving(analysis, AnalysisStatus.IDENTIFYING) == [True]
    assert moving(analysis, AnalysisStatus.IDENTIFYING) == [False]


def test_two_concurrent_claims_produce_one_winner(analysis: UUID) -> None:
    """Two workers, two connections, one row — settled by PostgreSQL, not by Python.

    The second `UPDATE` blocks on the first's row lock and then re-evaluates its
    own `WHERE`, which by then no longer matches. That is the whole concurrency
    story, and it is why this is one statement rather than a read and a write.
    """
    moving(analysis, AnalysisStatus.UPLOADING, AnalysisStatus.UPLOADED)

    async def both() -> list[bool]:
        engine = create_async_engine(DATABASE_URL or "")
        factory = create_session_factory(engine)
        try:

            async def claim() -> bool:
                async with factory() as db:
                    moved = await transition(db, analysis, to=AnalysisStatus.IDENTIFYING)
                    await db.commit()
                    return moved

            return list(await asyncio.gather(claim(), claim()))
        finally:
            await engine.dispose()

    assert sorted(run(both)) == [False, True]


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "path"),
    [
        (AnalysisStatus.FAILED, ()),
        (
            AnalysisStatus.COMPLETED,
            (
                AnalysisStatus.UPLOADING,
                AnalysisStatus.UPLOADED,
                AnalysisStatus.IDENTIFYING,
                AnalysisStatus.AWAITING_CONFIRMATION,
                AnalysisStatus.ANALYZING,
                AnalysisStatus.CALCULATING,
            ),
        ),
    ],
    ids=["failed", "completed"],
)
def test_reaching_a_terminal_state_stamps_when(
    analysis: UUID, target: AnalysisStatus, path: tuple[AnalysisStatus, ...]
) -> None:
    """The table's CHECK permits a terminal row with no `completed_at`; it would be useless.

    The timestamp is the database's `now()`, so an analysis is finished when the
    row says so rather than when the worker's clock did.
    """
    moving(analysis, *path)

    moving(analysis, target)

    status, completed_at = state_of(analysis)
    assert status == target.value
    assert completed_at is not None


def test_an_unfinished_analysis_has_no_completion_time(analysis: UUID) -> None:
    assert state_of(analysis) == ("created", None)
    moving(analysis, AnalysisStatus.UPLOADING)
    assert state_of(analysis)[1] is None


# ---------------------------------------------------------------------------
# The store is down
# ---------------------------------------------------------------------------


def test_an_unreachable_store_raises_the_stores_own_error() -> None:
    """Not a driver exception. `state.py` shares `sessions.execute` for exactly this."""

    async def scenario() -> None:
        engine = create_async_engine("postgresql+asyncpg://tcg:tcg@127.0.0.1:1/tcg")
        try:
            async with create_session_factory(engine)() as db:
                await transition(db, uuid.uuid4(), to=AnalysisStatus.UPLOADING)
        finally:
            await engine.dispose()

    with pytest.raises(AnalysisStoreUnavailable):
        run(scenario)
