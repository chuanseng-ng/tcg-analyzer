"""The deduplication pass end to end — #155.

Where `test_datasets_fingerprints.py` asserts what the hash does to an artifact,
this asserts what happens to a *photograph*: located, straightened and hashed
through `ml/card-detection` and `ml/normalization`, which is the path that ships.
It therefore imports the CV stack at module scope on purpose — this module and
`tcg_api.datasets.deduplication` are the two that may.

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
from tcg_api.datasets.deduplication import (
    HASH_VERSION,
    _parser,
    _validated,
    fingerprint_artifact,
    fingerprint_pending,
)
from tcg_api.datasets.fingerprints import (
    NEAR_DUPLICATE_DISTANCE,
    Fingerprint,
    distance,
    read_fingerprints,
)
from tcg_api.datasets.tables import training_image_fingerprints, training_images
from tcg_shared.storage import StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

#: Portrait, and large enough that the detector's working downscale is a real
#: one — `ml/card-detection/tests` chose these numbers and this copies them
#: rather than importing, for the reason `test_datasets_ingestion.py` gives: a
#: test fixture shared across packages is a dependency nobody declared.
HEIGHT, WIDTH = 1600, 1200
CARD = (285, 360, 630, 880)


def printed(width: int, height: int, tone: int, seed: int) -> NDArray[np.uint8]:
    """A card face with artwork on it, varying by seed.

    `ml/card-detection`'s own helper prints one fixed checkerboard, which is right
    for a detector — it only needs *a* card — and wrong here, where the whole
    question is whether two faces look alike.
    """
    generator = np.random.default_rng(seed)
    face = np.full((height, width, 3), tone, np.uint8)
    # The artwork is inset, leaving the bright border every card has. That border
    # is not decoration here: the detector finds a card by its outline, and a
    # face whose random blocks run to the edge has no outline to find against a
    # dark surface.
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


def png(picture: NDArray[np.uint8]) -> bytes:
    return bytes(cv2.imencode(".png", picture)[1].tobytes())


def jpeg(picture: NDArray[np.uint8], quality: int = 55) -> bytes:
    return bytes(cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes())


def fingerprint(data: bytes) -> Fingerprint:
    """Through the real path: detect, normalize, hash."""
    hashes = fingerprint_artifact(data)
    assert hashes is not None, "no card was located in this fixture"
    return Fingerprint(uuid.uuid4(), perceptual_hash=hashes[0], perceptual_hash_rotated=hashes[1])


def apart(left: bytes, right: bytes) -> int:
    measured = distance(fingerprint(left), fingerprint(right))
    assert measured is not None
    return measured


# ---------------------------------------------------------------------------
# The real path: a photograph in, a hash out
# ---------------------------------------------------------------------------


def test_a_photograph_and_its_recompression_are_near_duplicates() -> None:
    """The issue's second required test, on the path that ships.

    The same shot through a lossy encoder is a different `sha256` and the same
    card — precisely what the unique constraint cannot catch.
    """
    picture = photograph()

    assert apart(png(picture), jpeg(picture)) <= NEAR_DUPLICATE_DISTANCE


def test_a_photograph_and_its_downscale_are_near_duplicates() -> None:
    picture = photograph()
    smaller = cv2.resize(picture, (WIDTH // 2, HEIGHT // 2), interpolation=cv2.INTER_AREA)

    assert apart(png(picture), png(smaller)) <= NEAR_DUPLICATE_DISTANCE


def test_the_same_card_photographed_upside_down_is_a_near_duplicate() -> None:
    """`quarter_turns` puts the short edge first and says nothing about which way up."""
    picture = photograph()

    assert apart(png(picture), png(cv2.rotate(picture, cv2.ROTATE_180))) <= NEAR_DUPLICATE_DISTANCE


def test_the_same_card_retaken_under_different_light_is_a_near_duplicate() -> None:
    """A different exposure of one card — the leakage §32 names, and §29 cannot see."""
    assert apart(png(photograph(face=230)), png(photograph(face=190))) <= NEAR_DUPLICATE_DISTANCE


def test_two_different_printings_photographed_alike_are_not_linked() -> None:
    """Same frame, same lighting, different artwork.

    Note what this does *not* claim: two different *copies of one printing* are
    linked, deliberately. See `test_datasets_fingerprints.py` for the argument.
    """
    assert apart(png(photograph(seed=1)), png(photograph(seed=2))) > NEAR_DUPLICATE_DISTANCE


def test_a_photograph_with_no_card_in_it_yields_no_hash() -> None:
    """The detector answers rather than raising, so this returns rather than raising."""
    generator = np.random.default_rng(7)
    blank = np.clip(
        np.full((HEIGHT, WIDTH, 3), 40, np.int16)
        + generator.integers(-8, 9, size=(HEIGHT, WIDTH, 1)),
        0,
        255,
    ).astype(np.uint8)

    assert fingerprint_artifact(png(blank)) is None


def test_bytes_that_do_not_decode_yield_no_hash() -> None:
    assert fingerprint_artifact(b"not an image") is None


def test_the_hash_version_names_the_detector_and_the_normalizer() -> None:
    """The artifact is what is hashed, so either stage moving invalidates every row."""
    from tcg_ml_card_detection import CARD_DETECTION_VERSION
    from tcg_ml_normalization import NORMALIZATION_VERSION

    assert CARD_DETECTION_VERSION in HASH_VERSION
    assert NORMALIZATION_VERSION in HASH_VERSION
    assert str(NEAR_DUPLICATE_DISTANCE) not in HASH_VERSION.split("+")


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def parse(*argv: str) -> Any:
    parser = _parser()
    arguments = parser.parse_args(argv)
    _validated(parser, arguments)
    return arguments


def test_the_threshold_defaults_to_the_pinned_one() -> None:
    assert parse().threshold == NEAR_DUPLICATE_DISTANCE


def test_a_threshold_outside_the_hashs_width_is_refused() -> None:
    with pytest.raises(SystemExit):
        parse("--threshold", "65")


def test_measure_needs_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "one.png"
    file.write_bytes(png(photograph()))

    with pytest.raises(SystemExit):
        parse("--measure", str(file))


def test_measure_reads_a_directory_and_writes_nothing(tmp_path: Path) -> None:
    """No database, no object store, no rows — the instrument, not the pass."""
    from tcg_api.datasets.deduplication import measure

    (tmp_path / "a.png").write_bytes(png(photograph(seed=1)))
    (tmp_path / "a-again.jpg").write_bytes(jpeg(photograph(seed=1)))
    (tmp_path / "b.png").write_bytes(png(photograph(seed=2)))

    measure(tmp_path, threshold=NEAR_DUPLICATE_DISTANCE)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["a-again.jpg", "a.png", "b.png"]


# ---------------------------------------------------------------------------
# The pass, against a real database
# ---------------------------------------------------------------------------


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
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
        "training_images, physical_copies, cards, sets"
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
    """One training image row and its object, without going through ingestion.

    The provenance gate is `test_datasets_ingestion.py`'s subject; here it is a
    precondition, so the row is written directly with a record that passes.
    """
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


def sweep(storage: InMemoryObjectStorage) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            return await fingerprint_pending(engine, storage)
        finally:
            await engine.dispose()

    return run(scenario)


def stored_fingerprints() -> tuple[Fingerprint, ...]:
    async def scenario() -> tuple[Fingerprint, ...]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await read_fingerprints(connection)
        finally:
            await engine.dispose()

    return run(scenario)


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.mark.integration
@requires_postgres
def test_the_pass_fingerprints_the_corpus_and_the_relationship_is_readable(
    storage: InMemoryObjectStorage,
) -> None:
    """The acceptance criterion: detected, recorded, and available to the splitter."""
    picture = photograph()
    first = store(storage, png(picture))
    second = store(storage, jpeg(picture))
    other = store(storage, png(photograph(seed=2)))

    result = sweep(storage)

    assert result.computed == 3
    assert result.unlocatable == 0
    assert result.unreadable == 0

    from tcg_api.datasets.fingerprints import near_duplicate_groups

    groups = near_duplicate_groups(stored_fingerprints())
    assert groups == (frozenset({first, second}),)
    assert other not in groups[0]


@pytest.mark.integration
@requires_postgres
def test_the_pass_fingerprints_only_what_has_no_current_fingerprint(
    storage: InMemoryObjectStorage,
) -> None:
    """Re-running is cheap: the second pass computes nothing."""
    store(storage, png(photograph()))

    first = sweep(storage)
    second = sweep(storage)

    assert first.computed == 1
    assert second.computed == 0
    assert second.already_current == 1


@pytest.mark.integration
@requires_postgres
def test_a_stale_hash_version_is_recomputed(storage: InMemoryObjectStorage) -> None:
    """A detector or normalizer bump invalidates every row, and this is how."""
    image_id = store(storage, png(photograph()))
    sweep(storage)
    execute(
        sa.update(training_image_fingerprints).values(
            hash_version="dhash-8x8-v0.0.1+something-older"
        )
    )

    result = sweep(storage)

    assert result.computed == 1
    assert fetch(sa.select(training_image_fingerprints.c.hash_version))[0].hash_version == (
        HASH_VERSION
    )
    assert stored_fingerprints()[0].training_image_id == image_id


@pytest.mark.integration
@requires_postgres
def test_an_image_with_no_card_in_it_is_recorded_without_a_hash(
    storage: InMemoryObjectStorage,
) -> None:
    """A row rather than a gap, so the next pass does not decode it again."""
    generator = np.random.default_rng(7)
    blank = np.clip(
        np.full((HEIGHT, WIDTH, 3), 40, np.int16)
        + generator.integers(-8, 9, size=(HEIGHT, WIDTH, 1)),
        0,
        255,
    ).astype(np.uint8)
    store(storage, png(blank))

    result = sweep(storage)

    assert result.computed == 1
    assert result.unlocatable == 1
    assert stored_fingerprints()[0].perceptual_hash is None
    assert sweep(storage).computed == 0


@pytest.mark.integration
@requires_postgres
def test_an_image_whose_object_is_missing_does_not_stop_the_pass(
    storage: InMemoryObjectStorage,
) -> None:
    """One unreadable object among three, and nothing is recorded for it."""
    store(storage, png(photograph(seed=1)))
    store(storage, png(photograph(seed=2)))
    orphan = store(storage, png(photograph(seed=3)))
    run(
        lambda: storage.delete(
            StorageKey(
                fetch(
                    sa.select(training_images.c.original_uri).where(training_images.c.id == orphan)
                )[0].original_uri
            )
        )
    )

    result = sweep(storage)

    assert result.computed == 2
    assert result.unreadable == 1
    assert {row.training_image_id for row in fetch(sa.select(training_image_fingerprints))} == {
        row.id
        for row in fetch(sa.select(training_images.c.id).where(training_images.c.id != orphan))
    }
