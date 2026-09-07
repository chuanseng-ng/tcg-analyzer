"""The orphan sweep against real PostgreSQL — issue #264.

What the sweep has to get right is which keys the database still names, and
that is a property of a statement PostgreSQL executes — including the clock,
which is the database's and never this process's. The object store is
`InMemoryObjectStorage`, `test_retention.py`'s choice and for its reason: MinIO
proves `list` and `delete` in `packages/shared/tests/test_storage_contract.py`,
and what is under test here is the *set* the sweep computes.

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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.images import NORMALIZED_NAMESPACE, UPLOAD_NAMESPACE
from tcg_api.analysis.orphans import LOOKBACK_DAYS, ORPHAN_MARGIN_SECONDS, sweep_orphans
from tcg_api.analysis.tables import analyses, analysis_sessions, images
from tcg_api.database import create_session_factory
from tcg_domain.analysis import AnalysisStatus, ImageSide
from tcg_shared.storage import InMemoryObjectStorage, StorageKey, day_prefix

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
DIGEST = "d" * 64

#: The retention period the sweep is told about. Seven days is the product's,
#: but nothing here depends on the number — only on which side of it a key's
#: day prefix falls.
TTL_SECONDS = 7 * 24 * 3600

#: A day comfortably inside the window: past the cutoff, and not so far past it
#: that `LOOKBACK_DAYS` has stopped walking.
EXPIRED = timedelta(seconds=TTL_SECONDS + ORPHAN_MARGIN_SECONDS + 24 * 3600)

#: A day the window has already walked past. Nothing reaches it any more, which
#: is the bound this sweep deliberately accepts.
FORGOTTEN = EXPIRED + timedelta(days=LOOKBACK_DAYS + 1)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def days_ago(delta: timedelta) -> date:
    return (datetime.now(UTC) - delta).date()


def key_on(namespace: str, day: date) -> StorageKey:
    return StorageKey(f"{day_prefix(namespace, day)}{uuid.uuid4()}")


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


def seed(*, original: StorageKey, normalized: StorageKey | None) -> None:
    """Write one session, one analysis and one front image naming those keys."""
    session_id, analysis_id = uuid.uuid4(), uuid.uuid4()

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
                        "status": AnalysisStatus.UPLOADED.value,
                    },
                )
                await connection.execute(
                    sa.insert(images),
                    {
                        "id": uuid.uuid4(),
                        "analysis_id": analysis_id,
                        "side": ImageSide.FRONT.value,
                        "original_uri": str(original),
                        "normalized_uri": None if normalized is None else str(normalized),
                        "mime_type": JPEG,
                        "sha256": DIGEST,
                    },
                )
        finally:
            await engine.dispose()

    run(scenario)


def sweep(storage: InMemoryObjectStorage, *, limit: int = 100) -> int:
    async def scenario() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with create_session_factory(engine)() as db:
                return await sweep_orphans(db, storage, ttl_seconds=TTL_SECONDS, limit=limit)
        finally:
            await engine.dispose()

    return run(scenario)


def stored(*keys: StorageKey) -> InMemoryObjectStorage:
    storage = InMemoryObjectStorage()

    async def scenario() -> None:
        for key in keys:
            await storage.put(key, b"bytes", content_type=JPEG)

    run(scenario)
    return storage


# ---------------------------------------------------------------------------
# What the sweep removes
# ---------------------------------------------------------------------------


def test_an_expired_object_no_row_names_is_deleted() -> None:
    """The leak `quality.py` documents: a run died before its row was committed."""
    orphan = key_on(NORMALIZED_NAMESPACE, days_ago(EXPIRED))
    storage = stored(orphan)

    assert sweep(storage) == 1
    assert orphan not in storage.objects


def test_both_swept_namespaces_are_walked() -> None:
    uploaded = key_on(UPLOAD_NAMESPACE, days_ago(EXPIRED))
    artifact = key_on(NORMALIZED_NAMESPACE, days_ago(EXPIRED))
    storage = stored(uploaded, artifact)

    assert sweep(storage) == 2
    assert storage.objects == {}


# ---------------------------------------------------------------------------
# What it leaves alone — every one of these is a photograph it must not delete
# ---------------------------------------------------------------------------


def test_a_key_a_row_still_names_is_kept() -> None:
    """The whole safety argument: the backlog is swept by rows, not by prefix."""
    named = key_on(UPLOAD_NAMESPACE, days_ago(EXPIRED))
    orphan = key_on(UPLOAD_NAMESPACE, days_ago(EXPIRED))
    seed(original=named, normalized=None)
    storage = stored(named, orphan)

    assert sweep(storage) == 1
    assert set(storage.objects) == {named}


def test_a_normalized_artifact_a_row_names_is_kept() -> None:
    """Both columns, not just the original — `retention.py`'s rule."""
    original = key_on(UPLOAD_NAMESPACE, days_ago(EXPIRED))
    artifact = key_on(NORMALIZED_NAMESPACE, days_ago(EXPIRED))
    seed(original=original, normalized=artifact)
    storage = stored(original, artifact)

    assert sweep(storage) == 0
    assert set(storage.objects) == {original, artifact}


def test_an_object_minted_today_is_kept() -> None:
    """A run in flight has put its artifact and not yet committed its row."""
    live = key_on(NORMALIZED_NAMESPACE, days_ago(timedelta(0)))
    storage = stored(live)

    assert sweep(storage) == 0
    assert set(storage.objects) == {live}


def test_an_object_inside_the_margin_is_kept() -> None:
    """A session opened at 23:59 names objects under the day it started on and
    expires the next; the margin is what keeps the sweep off that boundary."""
    recent = key_on(UPLOAD_NAMESPACE, days_ago(timedelta(seconds=TTL_SECONDS)))
    storage = stored(recent)

    assert sweep(storage) == 0
    assert set(storage.objects) == {recent}


@pytest.mark.parametrize("namespace", ["training", "training-normalized"])
def test_the_corpus_namespaces_are_outside_the_walked_prefixes(namespace: str) -> None:
    """Retaining a training image is a separately justified purpose (ADR 0008),
    and its objects are not named by an `images` row at all."""
    corpus = key_on(namespace, days_ago(EXPIRED))
    storage = stored(corpus)

    assert sweep(storage) == 0
    assert set(storage.objects) == {corpus}


def test_a_day_older_than_the_window_is_not_reached() -> None:
    """The accepted bound, asserted rather than left to be discovered."""
    forgotten = key_on(UPLOAD_NAMESPACE, days_ago(FORGOTTEN))
    storage = stored(forgotten)

    assert sweep(storage) == 0
    assert set(storage.objects) == {forgotten}


# ---------------------------------------------------------------------------
# Bounds and repetition
# ---------------------------------------------------------------------------


def test_the_limit_bounds_one_run() -> None:
    orphans = [key_on(UPLOAD_NAMESPACE, days_ago(EXPIRED)) for _ in range(5)]
    storage = stored(*orphans)

    assert sweep(storage, limit=2) == 2
    assert len(storage.objects) == 3


def test_sweeping_twice_finishes_the_job_and_then_does_nothing() -> None:
    orphans = [key_on(NORMALIZED_NAMESPACE, days_ago(EXPIRED)) for _ in range(3)]
    storage = stored(*orphans)

    assert sweep(storage, limit=2) == 2
    assert sweep(storage, limit=2) == 1
    assert sweep(storage, limit=2) == 0
    assert storage.objects == {}
