"""Spec §32's train/validation/test split, without leakage.

§32 is explicit that random splitting is wrong here: near-identical photographs
of one physical card landing on both sides of the boundary makes the test score
a measurement of memorisation rather than of generalisation. So this module
**groups first and splits the groups** — assigning images and repairing
collisions afterwards is the same bug with more steps.

Three relations put two images in one group, and they compose:

1. a shared `physical_copy_id` — §32's primary key, and #153's whole reason for
   `physical_copies` existing;
2. a shared `source`, for the images where `physical_copy_id` is NULL. That is
   ADR 0008's approved class 4, this product's own consented uploads, where an
   anonymous session (spec §53) cannot tell two analyses of one card apart. §32
   lists `source` among its acceptable keys precisely for this case, and a
   grouping key that is honestly coarse beats one that is confidently wrong;
3. #155's near-duplicate groups, so two images the provenance did not link but
   the hash did stay together anyway.

**Nothing is persisted here and no version is created.** This is a function over
grouping keys; #157 writes what it returns into `dataset_versions` and
`dataset_members`, inside one transaction, which is what makes a member
impossible to add afterwards.

**Nothing here imports OpenCV or any `tcg_ml_*` package.**
:mod:`tcg_api.datasets.fingerprints` is the pure half of #155 for exactly this
reason — reading stored hashes needs no CV stack, and importing
:mod:`tcg_api.datasets.deduplication` would make the splitter a worker-image
command for a step it never runs. `services/api/tests/test_import_purity.py`
holds that.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_domain.dataset import DatasetSplit

from tcg_api.datasets.fingerprints import near_duplicate_groups, read_fingerprints
from tcg_api.datasets.tables import training_images

__all__ = [
    "DEFAULT_PROPORTIONS",
    "CorpusImage",
    "SplitAssignment",
    "assign_splits",
    "read_corpus",
    "split_corpus",
]

logger = logging.getLogger(__name__)

#: The shares to aim at, as whole-number weights rather than percentages, so the
#: deficit arithmetic below is exact `Fraction` work with no float anywhere.
#: Declaration order is also the tie-break order, which is why `train` is first:
#: on an empty board every split is equally short and the largest share should
#: get the first group.
#:
#: **These are targets and the splitter does not force them.** Groups have
#: different sizes, and a split that hit 70/15/15 exactly has almost certainly
#: broken one apart. What was actually achieved comes back on the result.
DEFAULT_PROPORTIONS: Final[tuple[tuple[DatasetSplit, int], ...]] = (
    (DatasetSplit.TRAIN, 70),
    (DatasetSplit.VALIDATION, 15),
    (DatasetSplit.TEST, 15),
)


@dataclass(frozen=True, slots=True)
class CorpusImage:
    """One training image, reduced to what §32 may group on.

    Args:
        training_image_id: The row this assignment will be about.
        physical_copy_id: Which physical object it photographs, or ``None`` where
            nothing identifies one.
        source: Where it came from — the fallback key, and never blank:
            `source_is_not_blank` refuses that one table over.

    **There is deliberately no `card_id`.** Two copies of one Base Set Charizard
    share it and §32 requires them to be splittable apart, so the catalog link is
    not merely unused here — it is not readable here.
    """

    training_image_id: uuid.UUID
    physical_copy_id: uuid.UUID | None
    source: str


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    """What one seeded pass decided, and what it actually achieved.

    Args:
        assignment: Every image's split. Total over the corpus it was given.
        group_sizes: The sizes of the groups that were assigned, largest first.
            The census is here rather than derived by the caller because #155's
            transitive merge is unbounded: a group of four hundred is a finding,
            and this is where it becomes visible.
        seed: The seed that produced this. #157 stores it on the version, which
            is the only thing that makes the split re-derivable years later.
    """

    assignment: Mapping[uuid.UUID, DatasetSplit]
    group_sizes: tuple[int, ...]
    seed: int

    @property
    def counts(self) -> dict[DatasetSplit, int]:
        """How many images each split received. Every split appears, zeros included."""
        tally = dict.fromkeys(DatasetSplit, 0)
        for split in self.assignment.values():
            tally[split] += 1
        return tally

    @property
    def proportions(self) -> dict[DatasetSplit, Fraction]:
        """The shares actually achieved — reported, never assumed.

        Exact `Fraction`s, so "did this hit the target?" is answerable rather
        than approximately answerable. An empty corpus is zeros rather than a
        division by zero.
        """
        counts = self.counts
        total = sum(counts.values())
        if total == 0:
            return dict.fromkeys(DatasetSplit, Fraction(0))
        return {split: Fraction(count, total) for split, count in counts.items()}


def assign_splits(
    images: Sequence[CorpusImage],
    *,
    seed: int,
    near_duplicates: Iterable[frozenset[uuid.UUID]] = (),
    proportions: Sequence[tuple[DatasetSplit, int]] = DEFAULT_PROPORTIONS,
) -> SplitAssignment:
    """Group the corpus, then hand each whole group to one split.

    Args:
        images: The corpus. Order is irrelevant — the assignment is a function of
            the grouping keys and the seed, never of the order rows came back in.
        seed: Recorded on the result and stored by #157.
        near_duplicates: #155's groups, as
            :func:`~tcg_api.datasets.fingerprints.near_duplicate_groups` returns
            them. Passed in rather than computed, so the threshold has one home;
            identifiers that are not in `images` are ignored, because the hashes
            and the corpus are read in two statements and one can be ahead.
        proportions: The shares to aim at, as weights.

    Raises:
        ValueError: If `proportions` is empty, names a split twice, or gives any
            split a share that is not positive — a split nothing can be assigned
            to is a partition with a hole in it.

    The order groups are considered in is
    ``sha256(f"{seed}:{smallest member id}")`` rather than
    ``random.Random(seed).shuffle``. It is the same one line and it is stable
    across CPython versions, platforms and languages, which is what "reproduce
    this split years later" actually asks for; the Mersenne Twister's sequence is
    an implementation detail nobody promised.

    Each group then goes to whichever split is furthest below its share, measured
    as ``placed / weight`` in exact `Fraction`s and tied on declaration order.
    Largest-first would balance marginally better and would make the seed
    decorative, which `dataset_versions.split_seed` being NOT NULL says it is
    not.
    """
    weights = _validated(proportions)
    groups = _group(images, near_duplicates)

    placed: dict[DatasetSplit, int] = dict.fromkeys(weights, 0)
    order = {split: index for index, split in enumerate(weights)}
    assignment: dict[uuid.UUID, DatasetSplit] = {}

    for group in sorted(groups, key=lambda members: _shuffle_key(seed, members)):
        chosen = min(
            weights, key=lambda split: (Fraction(placed[split], weights[split]), order[split])
        )
        placed[chosen] += len(group)
        for member in group:
            assignment[member] = chosen

    return SplitAssignment(
        assignment=assignment,
        group_sizes=tuple(sorted((len(group) for group in groups), reverse=True)),
        seed=seed,
    )


def _validated(proportions: Sequence[tuple[DatasetSplit, int]]) -> dict[DatasetSplit, int]:
    if not proportions:
        raise ValueError("a split needs at least one split to assign to")
    weights = dict(proportions)
    if len(weights) != len(proportions):
        raise ValueError("each split may be given a share once")
    for split, weight in weights.items():
        if weight <= 0:
            raise ValueError(f"{split} was given a share of {weight}; every share must be positive")
    return weights


def _shuffle_key(seed: int, members: frozenset[uuid.UUID]) -> str:
    """A group's place in the order, stable for ever given the seed.

    The smallest member identifier names the group: it does not depend on how
    the group was discovered, only on what is in it.
    """
    smallest = min(str(member) for member in members)
    return hashlib.sha256(f"{seed}:{smallest}".encode()).hexdigest()


def _group(
    images: Sequence[CorpusImage],
    near_duplicates: Iterable[frozenset[uuid.UUID]],
) -> tuple[frozenset[uuid.UUID], ...]:
    """The connected components of §32's three relations, singletons included.

    A union-find, as `near_duplicate_groups` uses for the same reason: the
    relations compose, so a copy linked to an upload by a hash pulls that whole
    source group with it. That merge is unbounded and it is the safe direction —
    over-grouping costs a little balance, under-grouping leaks — and
    `group_sizes` is what makes a runaway visible.
    """
    parent: dict[uuid.UUID, uuid.UUID] = {
        image.training_image_id: image.training_image_id for image in images
    }

    def find(node: uuid.UUID) -> uuid.UUID:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: uuid.UUID, right: uuid.UUID) -> None:
        parent[find(left)] = find(right)

    first_of_key: dict[tuple[str, str], uuid.UUID] = {}
    for image in images:
        # The copy where there is one, the source where there is not. Two
        # namespaces, so a source that happened to spell a UUID could not
        # collide with a copy.
        key = (
            ("copy", str(image.physical_copy_id))
            if image.physical_copy_id is not None
            else ("source", image.source)
        )
        union(first_of_key.setdefault(key, image.training_image_id), image.training_image_id)

    for linked in near_duplicates:
        known = [member for member in linked if member in parent]
        for member in known[1:]:
            union(known[0], member)

    grouped: dict[uuid.UUID, set[uuid.UUID]] = collections.defaultdict(set)
    for member in parent:
        grouped[find(member)].add(member)
    return tuple(frozenset(members) for members in grouped.values())


async def read_corpus(connection: AsyncConnection) -> tuple[CorpusImage, ...]:
    """Every training image, reduced to §32's two grouping keys.

    The whole table: a dataset version is a cut of the corpus as it stands, and
    there is no narrowing predicate to apply. Ordered so a reader of a query log
    sees something stable — the assignment itself does not depend on it.
    """
    rows = await connection.execute(
        sa.select(
            training_images.c.id,
            training_images.c.physical_copy_id,
            training_images.c.source,
        ).order_by(training_images.c.created_at, training_images.c.id)
    )
    return tuple(
        CorpusImage(
            training_image_id=row.id,
            physical_copy_id=row.physical_copy_id,
            source=row.source,
        )
        for row in rows
    )


async def split_corpus(
    connection: AsyncConnection,
    *,
    seed: int,
    proportions: Sequence[tuple[DatasetSplit, int]] = DEFAULT_PROPORTIONS,
) -> SplitAssignment:
    """Read the corpus and its fingerprints, and split it.

    The threshold is :data:`~tcg_api.datasets.fingerprints.NEAR_DUPLICATE_DISTANCE`
    and is not a parameter here: `tcg-detect-duplicate-training-images
    --threshold` moves what a *report* says, and the pinned default is what an
    assignment uses. An image with no stored fingerprint is still assigned — the
    pass may not have run, or may have found no card in it, and neither is a
    reason to drop a row from the corpus.
    """
    images = await read_corpus(connection)
    duplicates = near_duplicate_groups(await read_fingerprints(connection))
    assignment = assign_splits(
        images, seed=seed, near_duplicates=duplicates, proportions=proportions
    )

    counts = assignment.counts
    logger.info(
        "split %d image(s) in %d group(s) at seed %d: %s",
        len(images),
        len(assignment.group_sizes),
        seed,
        ", ".join(f"{split} {counts[split]}" for split, _ in proportions),
    )
    if assignment.group_sizes:
        logger.info(
            "largest group %d image(s); %d near-duplicate group(s) folded in",
            assignment.group_sizes[0],
            len(duplicates),
        )
    for split, _ in proportions:
        if counts[split] == 0:
            logger.warning(
                "the %s split is empty: %d group(s) cannot be partitioned into %d share(s) "
                "without breaking one, and breaking one is the leakage spec §32 forbids",
                split,
                len(assignment.group_sizes),
                len(proportions),
            )
    return assignment
