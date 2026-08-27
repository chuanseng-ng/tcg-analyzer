"""The retention sweep against a real PostgreSQL — issue #41, spec §54.

The claim this file has to make is that expired photographs are gone from
*both* stores, and the two markers cannot be combined to make it: `integration`
runs in a CI job with PostgreSQL and no MinIO, `object_storage` in one with
MinIO and no PostgreSQL. So the split is deliberate and the proof is in three
places:

* here — the sweep's logic, against a real database and a real cascade, with
  the object store in memory so the assertion "the object is gone" is a direct
  read of what the store holds;
* `packages/shared/tests/test_storage_contract.py` — that `delete` really
  removes an object from MinIO, and that deleting an absent key succeeds;
* CI's `compose` job — the whole thing end to end, upload to empty bucket.

The test worth reading twice is `test_a_storage_failure_leaves_the_row_due`.
The row is the only pointer to its objects, so a sweep that deletes rows first
and then fails leaves photographs nobody can find and nobody will ever delete —
spec §54's failure reached through spec §54's own mechanism. That test is the
ordering, asserted rather than described.

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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tcg_api.analysis.retention import purge_expired
from tcg_api.analysis.tables import analyses, analysis_sessions, images
from tcg_api.economics.tables import economic_configurations
from tcg_domain.analysis import ImageSide
from tcg_shared.storage import InMemoryObjectStorage, ObjectStorage, StorageKey, StorageUnavailable

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
DIGEST = "b" * 64
#: Far enough in the past that no clock skew makes it ambiguous, and paired with
#: a `created_at` of its own: `expires_after_it_was_created` refuses a row
#: inserted already expired, which is the constraint doing its job.
LAPSED = datetime(2020, 1, 1, tzinfo=UTC)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    """Drive one async scenario, as `test_storage_contract.py` does."""
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
def empty_tables():
    def truncate() -> None:
        async def scenario() -> None:
            engine = create_async_engine(DATABASE_URL or "")
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.text(
                            "TRUNCATE analysis_sessions, economic_configurations "
                            "RESTART IDENTITY CASCADE"
                        )
                    )
            finally:
                await engine.dispose()

        run(scenario)

    truncate()
    yield
    truncate()


def seed(
    *,
    due: bool,
    sides: dict[ImageSide, tuple[str, str | None]] | None = None,
    configured: bool = False,
) -> uuid.UUID:
    """Write one session, one analysis and its images. Returns the session id.

    `sides` maps a side to `(original_uri, normalized_uri)`; the columns hold a
    bare storage key despite the name, which is what the sweep turns back into a
    `StorageKey`.

    `configured` attaches an economic configuration, which is the one thing
    hanging off an analysis that the cascade does **not** reach: the analysis
    references it rather than owning it, so the sweep has to delete it itself.
    """
    session_id = uuid.uuid4()
    analysis_id = uuid.uuid4()
    configuration_id = uuid.uuid4() if configured else None
    lifetime = (
        {"created_at": LAPSED - timedelta(days=7), "expires_at": LAPSED}
        if due
        else {"expires_at": datetime.now(UTC) + timedelta(days=7)}
    )

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
                        **lifetime,
                    },
                )
                if configuration_id is not None:
                    await connection.execute(
                        sa.insert(economic_configurations),
                        {
                            "id": configuration_id,
                            "currency": "SGD",
                            # What the user says they paid — the reason this row
                            # is swept rather than left behind.
                            "acquisition_cost": Decimal("120.00"),
                            "grading_fee": Decimal("40.00"),
                            "outbound_shipping": Decimal("30.00"),
                            "return_shipping": Decimal("30.00"),
                            "insurance": Decimal("0.00"),
                            "miscellaneous": Decimal("0.00"),
                            "selling_fee_rate": Decimal("0.1000"),
                            "selling_fee_flat": Decimal("0.00"),
                            "grading_companies": ["psa"],
                            "optimization_mode": "expected_profit",
                            "minimum_image_quality": 0.5,
                            "minimum_grade_confidence": 0.5,
                            "minimum_figure_confidence": 0.4,
                            "maximum_unpriced_probability": 0.25,
                            "minimum_incremental_profit": Decimal("5.00"),
                        },
                    )
                await connection.execute(
                    sa.insert(analyses),
                    {
                        "id": analysis_id,
                        "session_id": session_id,
                        "economic_configuration_id": configuration_id,
                    },
                )
                for side, (original, normalized) in (sides or {}).items():
                    await connection.execute(
                        sa.insert(images),
                        {
                            "id": uuid.uuid4(),
                            "analysis_id": analysis_id,
                            "side": side.value,
                            "original_uri": original,
                            "normalized_uri": normalized,
                            "mime_type": JPEG,
                            "sha256": DIGEST,
                        },
                    )
        finally:
            await engine.dispose()

    run(scenario)
    return session_id


def stored(*keys: str) -> InMemoryObjectStorage:
    """A store holding one small object under each key."""
    storage = InMemoryObjectStorage()

    async def scenario() -> None:
        for key in keys:
            await storage.put(StorageKey(key), b"photograph", content_type=JPEG)

    run(scenario)
    return storage


def sweep(storage: ObjectStorage, *, limit: int = 100) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with AsyncSession(engine) as db:
                return await purge_expired(db, storage, limit=limit)
        finally:
            await engine.dispose()

    return run(scenario)


def counts() -> tuple[int, int, int]:
    async def scenario() -> tuple[int, int, int]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                totals = []
                for table in (analysis_sessions, analyses, images):
                    result = await connection.execute(sa.select(sa.func.count()).select_from(table))
                    totals.append(result.scalar_one())
                return totals[0], totals[1], totals[2]
        finally:
            await engine.dispose()

    return run(scenario)


# ---------------------------------------------------------------------------
# What expiry actually removes
# ---------------------------------------------------------------------------
def test_an_expired_session_loses_its_objects_and_its_rows() -> None:
    """Both stores, which is the acceptance criterion in one assertion."""
    seed(
        due=True,
        sides={
            ImageSide.FRONT: ("uploads/2020/01/01/front", "normalized/2020/01/01/front"),
            ImageSide.BACK: ("uploads/2020/01/01/back", "normalized/2020/01/01/back"),
        },
    )
    storage = stored(
        "uploads/2020/01/01/front",
        "normalized/2020/01/01/front",
        "uploads/2020/01/01/back",
        "normalized/2020/01/01/back",
    )

    swept = sweep(storage)

    assert storage.objects == {}
    assert counts() == (0, 0, 0)
    assert (swept.sessions, swept.objects, swept.failed) == (1, 4, 0)


def test_a_normalized_artifact_goes_with_its_original() -> None:
    """The derived image is as much a picture of somebody's living room."""
    seed(due=True, sides={ImageSide.FRONT: ("uploads/a", "normalized/a")})
    storage = stored("uploads/a", "normalized/a")

    sweep(storage)

    assert storage.objects == {}


def test_a_row_with_no_artifact_yet_deletes_the_one_object_it_has() -> None:
    """`normalized_uri` is NULL until normalization runs, and NULL is not a key."""
    seed(due=True, sides={ImageSide.FRONT: ("uploads/a", None)})
    storage = stored("uploads/a")

    swept = sweep(storage)

    assert storage.objects == {}
    assert swept.objects == 1


def test_a_session_with_no_photographs_still_goes() -> None:
    """An analysis abandoned before an upload is still an expired analysis."""
    seed(due=True)

    swept = sweep(InMemoryObjectStorage())

    assert counts() == (0, 0, 0)
    assert (swept.sessions, swept.objects) == (1, 0)


# ---------------------------------------------------------------------------
# What it leaves alone
# ---------------------------------------------------------------------------
def test_a_live_session_is_untouched() -> None:
    seed(due=False, sides={ImageSide.FRONT: ("uploads/live", None)})
    storage = stored("uploads/live")

    swept = sweep(storage)

    assert StorageKey("uploads/live") in storage.objects
    assert counts() == (1, 1, 1)
    assert swept.sessions == 0


def test_only_the_expired_half_of_a_mixed_database_goes() -> None:
    seed(due=True, sides={ImageSide.FRONT: ("uploads/old", None)})
    seed(due=False, sides={ImageSide.FRONT: ("uploads/new", None)})
    storage = stored("uploads/old", "uploads/new")

    sweep(storage)

    assert set(storage.objects) == {StorageKey("uploads/new")}
    assert counts() == (1, 1, 1)


def test_the_limit_bounds_one_sweep() -> None:
    """A backlog is worked through over several ticks, not in one transaction."""
    for _ in range(3):
        seed(due=True)

    assert sweep(InMemoryObjectStorage(), limit=2).sessions == 2
    assert counts()[0] == 1


# ---------------------------------------------------------------------------
# Failure and repetition
# ---------------------------------------------------------------------------
def test_sweeping_twice_changes_nothing_and_raises_nothing() -> None:
    """Idempotent: `delete` succeeds on an absent key, and a gone row is not due."""
    seed(due=True, sides={ImageSide.FRONT: ("uploads/a", None)})
    storage = stored("uploads/a")

    sweep(storage)
    again = sweep(storage)

    assert (again.sessions, again.objects, again.failed) == (0, 0, 0)
    assert counts() == (0, 0, 0)


class _RefusesOneKey:
    """A store that cannot delete one particular key. Everything else works."""

    def __init__(self, storage: InMemoryObjectStorage, refuse: str) -> None:
        self.storage = storage
        self.refuse = StorageKey(refuse)

    async def delete(self, key: StorageKey) -> None:
        if key == self.refuse:
            raise StorageUnavailable("the object store could not be reached")
        await self.storage.delete(key)


def test_a_storage_failure_leaves_the_row_due() -> None:
    """The ordering, asserted.

    If the row went first, this object would now be unreachable: nothing names
    it, and a sweep that works from rows will never see it again. Leaving the
    row is what makes the next tick able to finish the job.
    """
    seed(due=True, sides={ImageSide.FRONT: ("uploads/stuck", "normalized/stuck")})
    inner = stored("uploads/stuck", "normalized/stuck")
    storage = _RefusesOneKey(inner, "normalized/stuck")

    swept = sweep(storage)  # type: ignore[arg-type]

    assert (swept.sessions, swept.failed) == (0, 1)
    assert counts() == (1, 1, 1)
    # The original was deleted before the artifact refused. That is not a leak:
    # the row still names both, so the next tick deletes an absent key — which
    # succeeds — and finishes.
    assert set(inner.objects) == {StorageKey("normalized/stuck")}


def test_one_failing_session_does_not_hold_up_the_others() -> None:
    """Per-session transactions, so a permanently bad key cannot stall retention.

    The batch is ordered by `expires_at`, so a batch-wide abort would re-pick
    the same row every tick and nothing would ever expire again.
    """
    seed(due=True, sides={ImageSide.FRONT: ("uploads/stuck", None)})
    seed(due=True, sides={ImageSide.FRONT: ("uploads/fine", None)})
    inner = stored("uploads/stuck", "uploads/fine")
    storage = _RefusesOneKey(inner, "uploads/stuck")

    swept = sweep(storage)  # type: ignore[arg-type]

    assert (swept.sessions, swept.failed) == (1, 1)
    assert set(inner.objects) == {StorageKey("uploads/stuck")}
    assert counts() == (1, 1, 1)


def configurations() -> int:
    async def scenario() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    sa.select(sa.func.count()).select_from(economic_configurations)
                )
                return int(result.scalar_one())
        finally:
            await engine.dispose()

    return run(scenario)


def test_an_expired_session_takes_its_economic_configuration_with_it() -> None:
    """The one row the cascade does not reach — #65.

    An analysis *references* its configuration, so `ON DELETE CASCADE` runs the
    other way and the row would outlive the session that produced it, holding
    what the user said they paid for their card. Spec §54 is about exactly that.
    """
    seed(due=True, configured=True)
    assert configurations() == 1

    sweep(InMemoryObjectStorage())

    assert configurations() == 0


def test_a_live_sessions_configuration_is_left_alone() -> None:
    """The sweep deletes what is due and nothing else."""
    seed(due=False, configured=True)

    sweep(InMemoryObjectStorage())

    assert configurations() == 1
