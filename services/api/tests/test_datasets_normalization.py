"""The normalization pass end to end — #159.

What `test_datasets_deduplication.py` does for a hash, this does for the
artifact itself: a photograph in, a stored 756x1056 PNG and two written columns
out. It imports the CV stack at module scope on purpose — this module,
`tcg_api.datasets.normalization` and `tcg_api.datasets.deduplication` are the
ones that may.

The database half is skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL:

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import sqlalchemy as sa
from numpy.typing import NDArray
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.normalization import ARTIFACT_NAMESPACE, artifact, normalize_pending
from tcg_api.datasets.tables import training_images
from tcg_shared.storage import StorageError, StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage
from tcg_shared.storage.port import ObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

#: `test_datasets_deduplication.py`'s numbers, copied rather than imported for
#: the reason it gives about `ml/card-detection`'s: a fixture shared across
#: modules is a dependency nobody declared.
HEIGHT, WIDTH = 1600, 1200
CARD = (285, 360, 630, 880)


def printed(width: int, height: int, tone: int, seed: int) -> NDArray[np.uint8]:
    generator = np.random.default_rng(seed)
    face = np.full((height, width, 3), tone, np.uint8)
    inset = width // 8
    panel = generator.integers(0, 255, (9, 6, 3), dtype=np.uint8)
    art = cv2.resize(panel, (width - 2 * inset, height - 2 * inset), interpolation=cv2.INTER_CUBIC)
    face[inset : height - inset, inset : width - inset] = art
    grain = generator.integers(-10, 10, (height, width, 1))
    lit = np.clip(face.astype(np.int16) * (tone / 255.0) + grain, 0, 255)
    return lit.astype(np.uint8)


def photograph(*, seed: int = 1, face: int = 230, surface: int = 40) -> NDArray[np.uint8]:
    generator = np.random.default_rng(seed + 1000)
    speckle = generator.integers(-8, 9, size=(HEIGHT, WIDTH, 1))
    picture = np.clip(np.full((HEIGHT, WIDTH, 3), surface, np.int16) + speckle, 0, 255).astype(
        np.uint8
    )
    left, top, width, height = CARD
    picture[top : top + height, left : left + width] = printed(width, height, face, seed)
    return picture


def blank() -> NDArray[np.uint8]:
    generator = np.random.default_rng(7)
    return np.clip(
        np.full((HEIGHT, WIDTH, 3), 40, np.int16)
        + generator.integers(-8, 9, size=(HEIGHT, WIDTH, 1)),
        0,
        255,
    ).astype(np.uint8)


def png(picture: NDArray[np.uint8]) -> bytes:
    return bytes(cv2.imencode(".png", picture)[1].tobytes())


# ---------------------------------------------------------------------------
# The pure half: a photograph in, an artifact out
# ---------------------------------------------------------------------------


def test_a_photograph_of_a_card_yields_an_artifact() -> None:
    straightened = artifact(png(photograph()))

    assert straightened is not None
    # The card at 12 px/mm inside #194's 2 mm margin: 756+48 x 1056+48.
    assert (straightened.width, straightened.height) == (804, 1104)
    assert straightened.data.startswith(b"\x89PNG")


def test_a_photograph_with_no_card_in_it_yields_no_artifact() -> None:
    """The detector answers rather than raising, so this returns rather than raising."""
    assert artifact(png(blank())) is None


def test_bytes_that_do_not_decode_yield_no_artifact() -> None:
    assert artifact(b"not an image") is None


def test_the_artifact_namespace_is_not_the_analysis_one() -> None:
    """Spec §54's sweep deletes an analysis's objects; a training image outlives them.

    Two prefixes rather than one is what makes a retention rule expressible as a
    prefix at all — the guarantee `generate_key`'s date partition exists for.
    """
    from tcg_api.analysis.quality import NORMALIZED_NAMESPACE

    assert ARTIFACT_NAMESPACE != NORMALIZED_NAMESPACE


def test_the_deduplication_pass_hashes_what_this_module_straightens() -> None:
    """One detect-then-straighten path, asserted rather than trusted.

    `deduplication.py`'s docstring forbids a fourth normalization path; this is
    what would fail if somebody re-inlined the two guards there.
    """
    import inspect

    from tcg_api.datasets import deduplication

    source = inspect.getsource(deduplication.fingerprint_artifact)

    assert "artifact(data)" in source
    assert "detect(" not in source
    assert "normalize(" not in source


# ---------------------------------------------------------------------------
# The pass over the corpus
# ---------------------------------------------------------------------------


def run(scenario: Callable[[], Awaitable[Any]]) -> Any:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base`, so bring it up here."""
    if not DATABASE_URL:
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    if not DATABASE_URL:
        yield
        return
    tables = (
        "training_image_fingerprints, dataset_members, dataset_versions, "
        "image_annotations, centering_measurements, training_images, physical_copies, "
        "cards, sets"
    )
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def execute(statement: Any, values: Any = None) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                if values:
                    await connection.execute(statement, values)
                else:
                    await connection.execute(statement)
        finally:
            await engine.dispose()

    run(scenario)


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return run(scenario)


def store(storage: InMemoryObjectStorage, data: bytes) -> uuid.UUID:
    """One training image row and its object, without going through ingestion."""
    image_id = uuid.uuid4()
    key = f"training/2026/08/29/{image_id}"
    run(lambda: storage.put(StorageKey(key), data, content_type="image/png"))
    execute(
        sa.insert(training_images),
        {
            "id": image_id,
            "side": "front",
            "original_uri": key,
            "sha256": f"{image_id.int:064x}"[:64],
            "mime_type": "image/png",
            "width": WIDTH,
            "height": HEIGHT,
            "source": "first_party",
            "acquisition_method": "photographed_before_submission",
            "license": "owned outright",
            "commercial_use_allowed": True,
            "derivative_use_allowed": True,
            "redistribution_allowed": False,
            "acquired_at": datetime(2026, 8, 1, tzinfo=UTC),
        },
    )
    return image_id


def sweep(storage: ObjectStorage) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            return await normalize_pending(engine, storage)
        finally:
            await engine.dispose()

    return run(scenario)


def stored_row(image_id: uuid.UUID) -> Any:
    return fetch(
        sa.select(
            training_images.c.normalized_uri,
            training_images.c.normalization_details,
        ).where(training_images.c.id == image_id)
    )[0]


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.mark.integration
@requires_postgres
def test_the_pass_stores_an_artifact_and_records_how_it_was_made(
    storage: InMemoryObjectStorage,
) -> None:
    """The acceptance criterion: an annotator has something comparable to look at."""
    image_id = store(storage, png(photograph()))

    result = sweep(storage)

    assert (result.stored, result.unlocatable, result.unreadable) == (1, 0, 0)

    row = stored_row(image_id)
    assert row.normalized_uri.startswith(f"{ARTIFACT_NAMESPACE}/")
    assert row.normalization_details["width"] == 804
    assert row.normalization_details["height"] == 1104

    from tcg_ml_normalization import NORMALIZATION_VERSION

    assert row.normalization_details["version"] == NORMALIZATION_VERSION
    assert run(lambda: storage.get(StorageKey(row.normalized_uri))).startswith(b"\x89PNG")


@pytest.mark.integration
@requires_postgres
def test_a_stored_artifact_is_never_replaced(storage: InMemoryObjectStorage) -> None:
    """The whole reason the predicate is `normalized_uri IS NULL` and nothing else.

    #158 stores an annotation as a fraction of *the artifact the annotator saw*.
    Re-warping an image somebody has judged would move every stored coordinate
    without touching a row in `image_annotations`, so a second pass must be a
    no-op — and there is deliberately no `--force` to make it otherwise.
    """
    image_id = store(storage, png(photograph()))

    first = sweep(storage)
    before = stored_row(image_id).normalized_uri
    second = sweep(storage)

    assert first.stored == 1
    assert (second.stored, second.already_stored) == (0, 1)
    assert stored_row(image_id).normalized_uri == before
    assert len(storage.objects) == 2  # the photograph and its one artifact


@pytest.mark.integration
@requires_postgres
def test_an_image_with_no_locatable_card_is_counted_and_left_alone(
    storage: InMemoryObjectStorage,
) -> None:
    """Both columns stay NULL, which is what the viewer renders its fallback from."""
    image_id = store(storage, png(blank()))

    result = sweep(storage)

    assert (result.stored, result.unlocatable) == (0, 1)
    row = stored_row(image_id)
    assert row.normalized_uri is None
    assert row.normalization_details is None


@pytest.mark.integration
@requires_postgres
def test_an_unreadable_object_is_counted_and_does_not_stop_the_pass(
    storage: InMemoryObjectStorage,
) -> None:
    """One missing object must not cost the rest of the corpus its run."""

    class OneObjectIsGone:
        def __init__(self, inner: InMemoryObjectStorage, missing: str) -> None:
            self._inner = inner
            self._missing = missing

        async def get(self, key: StorageKey) -> bytes:
            if str(key) == self._missing:
                raise StorageError("the store did not answer")
            return await self._inner.get(key)

        async def put(self, key: StorageKey, data: bytes, *, content_type: str) -> None:
            await self._inner.put(key, data, content_type=content_type)

    broken = store(storage, png(photograph(seed=3)))
    intact = store(storage, png(photograph(seed=1)))
    missing = str(
        fetch(sa.select(training_images.c.original_uri).where(training_images.c.id == broken))[
            0
        ].original_uri
    )

    result = sweep(OneObjectIsGone(storage, missing))  # type: ignore[arg-type]

    assert (result.stored, result.unreadable) == (1, 1)
    assert stored_row(broken).normalized_uri is None
    assert stored_row(intact).normalized_uri is not None


class RefusesToBegin:
    """An engine that reads happily and refuses every write transaction.

    Enough of `AsyncEngine` for `normalize_pending`, and no more: it reads its
    pending rows through `connect()` and writes each artifact through `begin()`.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def connect(self) -> Any:
        return self._inner.connect()

    def begin(self) -> Any:
        raise sa.exc.OperationalError("UPDATE", None, Exception("no writes today"))


@pytest.mark.integration
@requires_postgres
def test_the_bytes_are_stored_before_the_row_names_them(
    storage: InMemoryObjectStorage,
) -> None:
    """A committed row must always name bytes that are there.

    `quality._store_artifact`'s order, and the opposite of #154's: ingestion
    writes the row first so a refused duplicate stores nothing, where here the
    row is an update that cannot be refused and the object is the thing that has
    to exist first. The orphan this leaves behind is what the module's docstring
    accepts, and it is the safe direction.
    """
    store(storage, png(photograph()))

    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            with pytest.raises(sa.exc.SQLAlchemyError):
                await normalize_pending(RefusesToBegin(engine), storage)  # type: ignore[arg-type]
        finally:
            await engine.dispose()

    run(scenario)

    assert any(str(key).startswith(f"{ARTIFACT_NAMESPACE}/") for key in storage.objects)
