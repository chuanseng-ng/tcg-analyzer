"""Rebuilding a corpus from its published manifests — #348.

The claim: a corpus database lost with its volume comes back from the committed
manifests plus the original photographs, and `--regenerate` then reproduces
each manifest byte for byte. Everything else here is a refusal that keeps a
rebuild from half-happening or happening twice.

The database half is skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL. Object storage is `InMemoryObjectStorage`, ingestion's substitution.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.ingestion import ProvenanceRefused, TrainingImageProvenance, ingest_card
from tcg_api.datasets.restore import Photograph, RestoreRefused, read_copies, restore
from tcg_api.datasets.splitting import split_corpus
from tcg_api.datasets.tables import (
    centering_measurements,
    dataset_versions,
    grading_outcomes,
    image_annotations,
    training_images,
)
from tcg_api.datasets.versioning import create_version, read_manifest, render_manifest
from tcg_shared.storage import StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to read from",
)

PROVENANCE = TrainingImageProvenance(
    source="first_party",
    acquisition_method="photographed_before_submission",
    license="owned outright",
    commercial_use_allowed=True,
    derivative_use_allowed=True,
    redistribution_allowed=False,
    acquired_at=datetime(2026, 8, 29, 19, 2, tzinfo=UTC),
)


# ---------------------------------------------------------------------------
# The copies CSV
# ---------------------------------------------------------------------------
def test_copies_resolve_against_the_csvs_own_directory(tmp_path: Path) -> None:
    (tmp_path / "ditto").mkdir()
    (tmp_path / "ditto" / "front.png").write_bytes(b"x")
    copy = uuid.uuid4()
    csv = tmp_path / "copies.csv"
    csv.write_text(
        f"file,physical_copy_id,acquired_at\nditto/front.png,{copy},2026-08-29T19:02:00+08:00\n",
        encoding="utf-8",
    )

    (row,) = read_copies(csv)

    assert row.path == tmp_path / "ditto" / "front.png"
    assert row.physical_copy_id == copy
    assert row.acquired_at.utcoffset() is not None


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("front.png,not-a-uuid,2026-08-29T19:02:00+08:00", "physical_copy_id"),
        ("front.png,{copy},2026-08-29T19:02:00", "time zone"),
        ("missing.png,{copy},2026-08-29T19:02:00+08:00", "not a file"),
    ],
)
def test_a_bad_copies_row_is_refused_by_row(tmp_path: Path, line: str, reason: str) -> None:
    (tmp_path / "front.png").write_bytes(b"x")
    csv = tmp_path / "copies.csv"
    csv.write_text(
        "file,physical_copy_id,acquired_at\n" + line.format(copy=uuid.uuid4()) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RestoreRefused, match=reason):
        read_copies(csv)


# ---------------------------------------------------------------------------
# Against a live database
# ---------------------------------------------------------------------------
def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    if not DATABASE_URL:
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


def truncate() -> None:
    tables = "dataset_members, dataset_versions, grading_outcomes, training_images, physical_copies"
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    if not DATABASE_URL:
        yield
        return
    truncate()
    yield
    truncate()


def execute(statement: Any) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement)
        finally:
            await engine.dispose()

    run(scenario)


def count(table: sa.Table) -> int:
    async def scenario() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return (
                    await connection.execute(sa.select(sa.func.count()).select_from(table))
                ).scalar_one()
        finally:
            await engine.dispose()

    return run(scenario)


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 84), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def ingest(storage: InMemoryObjectStorage, front: bytes, back: bytes) -> uuid.UUID:
    async def scenario() -> uuid.UUID:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            copy_id, _ = await ingest_card(
                engine,
                storage,
                provenance=PROVENANCE,
                front=front,
                back=back,
                max_bytes=10_000_000,
                max_pixels=10_000_000,
            )
        finally:
            await engine.dispose()
        assert copy_id is not None
        return copy_id

    return run(scenario)


def publish(version: str, seed: int) -> str:
    async def scenario() -> str:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                assignment = await split_corpus(connection, seed=seed)
                await create_version(connection, version=version, assignment=assignment)
                return render_manifest(await read_manifest(connection, version=version))
        finally:
            await engine.dispose()

    return run(scenario)


def regenerate(version: str) -> str:
    async def scenario() -> str:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return render_manifest(await read_manifest(connection, version=version))
        finally:
            await engine.dispose()

    return run(scenario)


def do_restore(
    storage: InMemoryObjectStorage,
    manifests: list[str],
    photographs: list[Photograph],
    *,
    license: str | None = "owned outright",
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await restore(
                engine,
                storage,
                manifests=[json.loads(text) for text in manifests],
                photographs=photographs,
                license=license,
                commercial_use_allowed=True,
                derivative_use_allowed=True,
                annotator_id="annotator",
            )
        finally:
            await engine.dispose()

    run(scenario)


def a_corpus(storage: InMemoryObjectStorage) -> tuple[list[str], list[Photograph]]:
    """Two graded-or-annotated cards, published twice: ordinals 1 and 2."""
    pictures = [png((10 * i, 40, 200 - 10 * i)) for i in range(4)]
    first = ingest(storage, pictures[0], pictures[1])
    second = ingest(storage, pictures[2], pictures[3])

    rows = run(_images)
    by_copy = {row.physical_copy_id: row.id for row in rows}
    execute(
        sa.insert(image_annotations).values(
            id=uuid.uuid4(),
            training_image_id=by_copy[first],
            kind="corner",
            region="top_left",
            label="whitening",
            severity="minor",
            confidence=0.9,
            bbox_x=0.1,
            bbox_y=0.2,
            bbox_width=0.05,
            bbox_height=0.07,
            representation="normalized",
            annotator_id="annotator",
        )
    )
    execute(
        sa.insert(centering_measurements).values(
            id=uuid.uuid4(),
            training_image_id=by_copy[second],
            horizontal=0.5693255518416324,
            vertical=None,
            confidence=0.3,
            annotator_id="annotator",
        )
    )
    execute(
        sa.insert(grading_outcomes).values(
            id=uuid.uuid4(),
            physical_copy_id=second,
            grading_company="psa",
            certification_number="12345678",
            grade="9",
        )
    )

    manifests = [publish("pokemon-condition-v0.1.0", 1), publish("pokemon-condition-v0.2.0", 2)]
    photographs = [
        Photograph(data=pictures[index], physical_copy_id=copy, acquired_at=PROVENANCE.acquired_at)
        for index, copy in enumerate((first, first, second, second))
    ]
    return manifests, photographs


async def _images() -> list[Any]:
    engine = create_async_engine(DATABASE_URL or "")
    try:
        async with engine.connect() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(training_images.c.id, training_images.c.physical_copy_id)
                    )
                ).all()
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
@requires_postgres
def test_a_restored_corpus_regenerates_every_manifest_byte_for_byte() -> None:
    """The acceptance criterion, and the next publish continues the ordinals."""
    original = InMemoryObjectStorage()
    manifests, photographs = a_corpus(original)
    truncate()

    rebuilt = InMemoryObjectStorage()
    do_restore(rebuilt, manifests, photographs)

    assert [regenerate("pokemon-condition-v0.1.0"), regenerate("pokemon-condition-v0.2.0")] == (
        manifests
    )
    # Every original is back under the key the manifest names.
    for member in json.loads(manifests[1])["members"]:
        key = StorageKey(member["original_uri"])
        assert run(lambda key=key: rebuilt.get(key)) == run(lambda key=key: original.get(key))
    assert json.loads(publish("pokemon-condition-v0.3.0", 3))["ordinal"] == 3


@pytest.mark.integration
@requires_postgres
def test_a_manifest_rendered_before_outcomes_were_rendered_still_restores() -> None:
    """`pokemon-condition-v0.1.0.json` predates #220 and has no `grading_outcomes` key."""
    manifests, photographs = a_corpus(InMemoryObjectStorage())
    truncate()
    old = json.loads(manifests[0])
    for member in old["members"]:
        del member["grading_outcomes"]

    do_restore(InMemoryObjectStorage(), [json.dumps(old), manifests[1]], photographs)

    assert regenerate("pokemon-condition-v0.2.0") == manifests[1]


@pytest.mark.integration
@requires_postgres
def test_a_non_empty_target_is_refused_and_nothing_is_written() -> None:
    """A rerun refuses rather than duplicating a single row."""
    manifests, photographs = a_corpus(InMemoryObjectStorage())
    before = count(training_images), count(dataset_versions)

    storage = InMemoryObjectStorage()
    with pytest.raises(RestoreRefused, match="not empty"):
        do_restore(storage, manifests, photographs)

    assert (count(training_images), count(dataset_versions)) == before
    assert storage.objects == {}


@pytest.mark.integration
@requires_postgres
def test_a_member_with_no_matching_photograph_is_refused_before_any_write() -> None:
    manifests, photographs = a_corpus(InMemoryObjectStorage())
    truncate()

    storage = InMemoryObjectStorage()
    with pytest.raises(RestoreRefused, match="no photograph"):
        do_restore(storage, manifests, photographs[1:])

    assert storage.objects == {}
    assert count(training_images) == 0


@pytest.mark.integration
@requires_postgres
def test_a_photograph_no_manifest_names_is_refused_rather_than_re_encoded() -> None:
    manifests, photographs = a_corpus(InMemoryObjectStorage())
    truncate()
    stranger = Photograph(
        data=png((1, 2, 3)),
        physical_copy_id=photographs[0].physical_copy_id,
        acquired_at=PROVENANCE.acquired_at,
    )

    storage = InMemoryObjectStorage()
    with pytest.raises(RestoreRefused, match="no manifest"):
        do_restore(storage, manifests, [*photographs, stranger])

    assert storage.objects == {}


@pytest.mark.integration
@requires_postgres
def test_provenance_is_verified_before_anything_is_restored() -> None:
    manifests, photographs = a_corpus(InMemoryObjectStorage())
    truncate()

    storage = InMemoryObjectStorage()
    with pytest.raises(ProvenanceRefused):
        do_restore(storage, manifests, photographs, license="  ")

    assert storage.objects == {}
    assert count(training_images) == 0
