"""Spec §32's train/validation/test split — #156.

The central claim is the first test: two photographs of one physical card never
land on opposite sides of the boundary. Everything else in this file exists to
keep that true while the splitter is also seeded, balanced and honest about what
it achieved.

Nothing here imports the CV stack. The splitter reads stored grouping keys and
stored hashes, which is why it stays runnable outside the worker image —
`test_import_purity.py` asserts that separately.

The database half is skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.splitting import (
    DEFAULT_PROPORTIONS,
    CorpusImage,
    SplitAssignment,
    assign_splits,
    read_corpus,
    split_corpus,
)
from tcg_api.datasets.tables import physical_copies, training_image_fingerprints, training_images
from tcg_domain.dataset import DatasetSplit

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to read from",
)

#: An arbitrary but fixed seed. Every assertion about *where* something landed
#: depends on it, so it is named once rather than retyped per test.
SEED = 20260828


def _image(
    *,
    copy: uuid.UUID | None = None,
    source: str = "first_party",
) -> CorpusImage:
    return CorpusImage(
        training_image_id=uuid.uuid4(),
        physical_copy_id=copy,
        source=source,
    )


def _split_of(assignment: SplitAssignment, image: CorpusImage) -> DatasetSplit:
    return assignment.assignment[image.training_image_id]


def _distinct_copies(count: int, *, source: str = "first_party") -> list[CorpusImage]:
    """`count` images, each of its own physical copy — `count` singleton groups."""
    return [_image(copy=uuid.uuid4(), source=source) for _ in range(count)]


# ---------------------------------------------------------------------------
# The claim spec §32 makes
# ---------------------------------------------------------------------------
def test_two_photographs_of_one_physical_copy_never_land_in_different_splits() -> None:
    """§32's whole point. Written first, because everything else is in service of it.

    Front and back of one card, plus enough other groups that the splitter has a
    real choice to get wrong.
    """
    copy = uuid.uuid4()
    front = _image(copy=copy)
    back = _image(copy=copy)
    corpus = [front, back, *_distinct_copies(30)]

    assignment = assign_splits(corpus, seed=SEED)

    assert _split_of(assignment, front) == _split_of(assignment, back)


def test_a_physical_copy_holds_together_under_every_seed() -> None:
    """One seed agreeing could be luck; a hundred cannot.

    The grouping is applied before the assignment, so no seed can break a copy
    apart — a splitter that assigned images and repaired collisions afterwards
    would fail somewhere in this range.
    """
    copy = uuid.uuid4()
    front = _image(copy=copy)
    back = _image(copy=copy)
    corpus = [front, back, *_distinct_copies(12)]

    for seed in range(100):
        assignment = assign_splits(corpus, seed=seed)
        assert _split_of(assignment, front) == _split_of(assignment, back), seed


def test_two_images_the_deduplicator_linked_never_land_in_different_splits() -> None:
    """The near-duplicate half — a retake under different light is a new digest.

    The two images name different physical copies here on purpose: the hash
    found a relationship the provenance did not record, which is exactly the
    case #155 exists for, and the splitter has to honour it anyway.
    """
    retake = _image(copy=uuid.uuid4())
    original = _image(copy=uuid.uuid4())
    corpus = [retake, original, *_distinct_copies(30)]
    linked = [frozenset({retake.training_image_id, original.training_image_id})]

    assignment = assign_splits(corpus, seed=SEED, near_duplicates=linked)

    assert _split_of(assignment, retake) == _split_of(assignment, original)


def test_a_chain_of_near_duplicates_lands_whole() -> None:
    """A resembles B and B resembles C: all three move together.

    `near_duplicate_groups` already returns the transitive closure, so this
    passes two overlapping pairs instead — the splitter must not assume its
    input is disjoint.
    """
    a, b, c = (_image(copy=uuid.uuid4()) for _ in range(3))
    corpus = [a, b, c, *_distinct_copies(30)]
    linked = [
        frozenset({a.training_image_id, b.training_image_id}),
        frozenset({b.training_image_id, c.training_image_id}),
    ]

    assignment = assign_splits(corpus, seed=SEED, near_duplicates=linked)

    assert _split_of(assignment, a) == _split_of(assignment, b) == _split_of(assignment, c)


def test_two_different_copies_of_one_card_are_not_forced_together() -> None:
    """The catalog id is never a grouping key, and it is not even readable here.

    Two copies of one Base Set Charizard share a `card_id` and §32 requires them
    to be splittable apart. The structural half of the assertion is the stronger
    one: `CorpusImage` carries no `card_id` at all, so no future edit can start
    grouping on it by accident.
    """
    assert [field.name for field in dataclasses.fields(CorpusImage)] == [
        "training_image_id",
        "physical_copy_id",
        "source",
    ]

    corpus = _distinct_copies(40)
    assignment = assign_splits(corpus, seed=SEED)

    landed = {_split_of(assignment, image) for image in corpus}
    assert len(landed) == 3, "40 independent copies should reach all three splits"


# ---------------------------------------------------------------------------
# The fallback key, where no copy could be identified
# ---------------------------------------------------------------------------
def test_images_with_no_physical_copy_group_by_source() -> None:
    """ADR 0008's approved class 4 — a consented upload identifies no copy.

    §32 lists `source` among its acceptable keys precisely for this case, and a
    grouping key that is honestly coarse beats one that is confidently wrong:
    treating every unidentified image as its own copy is what leaks.
    """
    uploads = [_image(source="product_upload") for _ in range(6)]
    corpus = [*uploads, *_distinct_copies(30)]

    assignment = assign_splits(corpus, seed=SEED)

    landed = {_split_of(assignment, upload) for upload in uploads}
    assert len(landed) == 1


def test_two_sources_with_no_physical_copy_are_two_groups() -> None:
    """The fallback groups by source, not into one bucket of everything unidentified."""
    uploads = [_image(source="product_upload") for _ in range(20)]
    contributed = [_image(source="contributed") for _ in range(20)]

    assignment = assign_splits([*uploads, *contributed], seed=SEED)

    assert assignment.group_sizes == (20, 20)


def test_an_identified_copy_is_not_swallowed_by_its_source() -> None:
    """The fallback applies only where `physical_copy_id` is NULL.

    Otherwise every first-party photograph would be one group and the corpus
    would have nothing left to split.
    """
    corpus = _distinct_copies(20, source="first_party")

    assignment = assign_splits(corpus, seed=SEED)

    assert assignment.group_sizes == (1,) * 20


# ---------------------------------------------------------------------------
# Determinism — §57's discipline, applied to a corpus
# ---------------------------------------------------------------------------
def test_the_same_corpus_and_seed_give_the_same_assignment() -> None:
    corpus = _distinct_copies(50)

    first = assign_splits(corpus, seed=SEED)
    second = assign_splits(corpus, seed=SEED)

    assert first == second


def test_the_order_the_corpus_arrives_in_does_not_change_the_assignment() -> None:
    """A split that depended on row order would be reproducible only by accident."""
    corpus = _distinct_copies(50)

    forward = assign_splits(corpus, seed=SEED)
    backward = assign_splits(list(reversed(corpus)), seed=SEED)

    assert forward.assignment == backward.assignment


def test_a_different_seed_moves_something() -> None:
    """Otherwise `dataset_versions.split_seed` would be a column recording nothing."""
    corpus = _distinct_copies(50)

    assert (
        assign_splits(corpus, seed=SEED).assignment
        != assign_splits(corpus, seed=SEED + 1).assignment
    )


def test_the_seed_is_carried_on_the_result() -> None:
    """#157 stores it on the version, and reading it back off the result is how."""
    assert assign_splits(_distinct_copies(3), seed=SEED).seed == SEED


# ---------------------------------------------------------------------------
# What was achieved, rather than what was targeted
# ---------------------------------------------------------------------------
def test_every_image_is_assigned_exactly_once() -> None:
    corpus = [
        *_distinct_copies(20),
        *(_image(source="product_upload") for _ in range(5)),
    ]

    assignment = assign_splits(corpus, seed=SEED)

    assert set(assignment.assignment) == {image.training_image_id for image in corpus}
    assert sum(assignment.counts.values()) == len(corpus)


def test_many_small_groups_converge_on_the_targets() -> None:
    """300 singletons is the case where the proportions can be hit closely."""
    assignment = assign_splits(_distinct_copies(300), seed=SEED)

    for split, weight in DEFAULT_PROPORTIONS:
        target = Fraction(weight, sum(share for _, share in DEFAULT_PROPORTIONS))
        assert abs(assignment.proportions[split] - target) < Fraction(2, 100), split


def test_the_proportions_are_not_forced_when_the_groups_are_lumpy() -> None:
    """A splitter that hit 70/15/15 exactly here would have split a group.

    Two groups of forty and a scattering of singletons cannot be partitioned to
    the targets, and the result reports what it did rather than pretending.
    """
    lump_a = uuid.uuid4()
    lump_b = uuid.uuid4()
    corpus = [
        *(_image(copy=lump_a) for _ in range(40)),
        *(_image(copy=lump_b) for _ in range(40)),
        *_distinct_copies(20),
    ]

    assignment = assign_splits(corpus, seed=SEED)

    assert assignment.group_sizes == (40, 40, *(1,) * 20)
    assert sum(assignment.counts.values()) == 100
    assert assignment.proportions[DatasetSplit.TRAIN] != Fraction(70, 100)


def test_the_group_census_is_descending_so_a_runaway_group_is_visible() -> None:
    """#155's unbounded transitive merge is named rather than fixed; this is the sighting."""
    bridged = [_image(copy=uuid.uuid4()) for _ in range(5)]
    corpus = [*bridged, *_distinct_copies(3)]
    linked = [frozenset(image.training_image_id for image in bridged)]

    assignment = assign_splits(corpus, seed=SEED, near_duplicates=linked)

    assert assignment.group_sizes == (5, 1, 1, 1)


def test_a_corpus_of_one_group_is_a_defensible_answer() -> None:
    """Six photographs of one card cannot be split without leaking, so they are not.

    The group lands whole, the other two splits come back empty, and the counts
    say so — ADR 0008's risk R7 is that the corpus is small, and the splitter
    surfaces that rather than hiding it behind a test split of one.
    """
    copy = uuid.uuid4()
    corpus = [_image(copy=copy) for _ in range(6)]

    assignment = assign_splits(corpus, seed=SEED)

    assert assignment.group_sizes == (6,)
    assert assignment.counts[DatasetSplit.TRAIN] == 6
    assert assignment.counts[DatasetSplit.VALIDATION] == 0
    assert assignment.counts[DatasetSplit.TEST] == 0


def test_an_empty_corpus_is_an_empty_assignment() -> None:
    assignment = assign_splits([], seed=SEED)

    assert assignment.assignment == {}
    assert assignment.group_sizes == ()
    assert assignment.counts == dict.fromkeys(DatasetSplit, 0)
    assert assignment.proportions == dict.fromkeys(DatasetSplit, Fraction(0))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_a_zero_or_negative_share_is_refused() -> None:
    """A split nothing can be assigned to is a partition with a hole in it."""
    with pytest.raises(ValueError, match="positive"):
        assign_splits(
            _distinct_copies(3),
            seed=SEED,
            proportions=((DatasetSplit.TRAIN, 70), (DatasetSplit.TEST, 0)),
        )


def test_an_empty_set_of_proportions_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one split"):
        assign_splits(_distinct_copies(3), seed=SEED, proportions=())


def test_a_split_named_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="once"):
        assign_splits(
            _distinct_copies(3),
            seed=SEED,
            proportions=((DatasetSplit.TRAIN, 70), (DatasetSplit.TRAIN, 30)),
        )


def test_a_near_duplicate_group_naming_an_absent_image_is_ignored() -> None:
    """The hashes and the corpus are read in two statements; one can be ahead."""
    present = _image(copy=uuid.uuid4())
    corpus = [present, *_distinct_copies(3)]
    linked = [frozenset({present.training_image_id, uuid.uuid4()})]

    assignment = assign_splits(corpus, seed=SEED, near_duplicates=linked)

    assert set(assignment.assignment) == {image.training_image_id for image in corpus}


# ---------------------------------------------------------------------------
# Against a live database
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
    tables = "training_image_fingerprints, training_images, physical_copies"
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def execute(statement: Any) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement)
        finally:
            await engine.dispose()

    run(scenario)


def store(
    *,
    copy: uuid.UUID | None,
    source: str,
    hashes: tuple[str, str] | None = None,
) -> uuid.UUID:
    """One provenance-clean training image, and optionally its fingerprint."""
    image_id = uuid.uuid4()
    execute(
        sa.insert(training_images).values(
            id=image_id,
            physical_copy_id=copy,
            side="front",
            original_uri=f"training/{image_id}.png",
            # 64 lowercase hex characters, unique per image: a UUID is 32 of
            # them and this doubles it rather than reaching for a real digest.
            sha256=f"{image_id.hex}{image_id.hex}",
            mime_type="image/png",
            width=756,
            height=1056,
            source=source,
            acquisition_method="photographed_owned_slab",
            license="owned outright",
            commercial_use_allowed=True,
            derivative_use_allowed=True,
            redistribution_allowed=False,
            acquired_at=datetime.now(UTC),
        )
    )
    if hashes is not None:
        execute(
            sa.insert(training_image_fingerprints).values(
                training_image_id=image_id,
                perceptual_hash=hashes[0],
                perceptual_hash_rotated=hashes[1],
                hash_version="dhash-8x8-v0.1.0+fixture",
            )
        )
    return image_id


def copy_row() -> uuid.UUID:
    copy_id = uuid.uuid4()
    execute(sa.insert(physical_copies).values(id=copy_id))
    return copy_id


def read() -> tuple[CorpusImage, ...]:
    async def scenario() -> tuple[CorpusImage, ...]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await read_corpus(connection)
        finally:
            await engine.dispose()

    return run(scenario)


def split(*, seed: int) -> SplitAssignment:
    async def scenario() -> SplitAssignment:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await split_corpus(connection, seed=seed)
        finally:
            await engine.dispose()

    return run(scenario)


@pytest.mark.integration
@requires_postgres
def test_read_corpus_returns_the_two_grouping_keys_and_nothing_else() -> None:
    copy = copy_row()
    identified = store(copy=copy, source="first_party")
    unidentified = store(copy=None, source="product_upload")

    corpus = {image.training_image_id: image for image in read()}

    assert corpus[identified].physical_copy_id == copy
    assert corpus[identified].source == "first_party"
    assert corpus[unidentified].physical_copy_id is None
    assert corpus[unidentified].source == "product_upload"


@pytest.mark.integration
@requires_postgres
def test_split_corpus_folds_the_stored_fingerprints_in() -> None:
    """End to end: two copies the provenance did not link, whose hashes match.

    The two carry the same pair of hashes, so their distance is zero and they
    are within any threshold. Neither row says they are related — the schema has
    nowhere to say it — and the splitter has to keep them together anyway.
    """
    alike = ("0f1e2d3c4b5a6978", "69784b5a2d3c0f1e")
    left = store(copy=copy_row(), source="first_party", hashes=alike)
    right = store(copy=copy_row(), source="first_party", hashes=alike)
    for _ in range(20):
        store(copy=copy_row(), source="first_party")

    assignment = split(seed=SEED)

    assert assignment.assignment[left] == assignment.assignment[right]
    assert len(assignment.assignment) == 22
    assert sum(assignment.counts.values()) == 22
    # 22 images in 21 groups: the two alike ones merged, the rest are singletons.
    assert assignment.group_sizes == (2, *(1,) * 20)


@pytest.mark.integration
@requires_postgres
def test_an_image_with_no_fingerprint_is_still_assigned() -> None:
    """The pass has not run, or found no card. Neither is a reason to drop a row."""
    stored = [store(copy=copy_row(), source="first_party") for _ in range(5)]

    assignment = split(seed=SEED)

    assert set(assignment.assignment) == set(stored)
