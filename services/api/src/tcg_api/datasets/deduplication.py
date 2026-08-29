"""Spec §28's deduplication pass — the near-duplicate half, and its report.

Fingerprints every training image that has none, then reports the groups §32's
splitter must not break apart. The exact half is not here and needs no code:
`uq_training_images_sha256` makes a second ingest of identical bytes
unrepresentable, and `tcg_api.datasets.ingestion.main` already turns that
refusal into a sentence. Nothing in this module looks for one.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
    uv run tcg-detect-duplicate-training-images

    uv run tcg-detect-duplicate-training-images --measure ~/cards

**This runs from the worker image only.** Hashing needs the standardized
artifact, which needs `ml/card-detection` and `ml/normalization`, which is
`services/api`'s `worker` extra — the API image installs none of it, and
`services/api/tests/test_import_purity.py` keeps that true. This module is
therefore the one that binds to OpenCV, exactly as `tcg_api.analysis.quality`
does, and nothing the application imports may reach it. The pure half —
the hash itself, the distance, the grouping — is
:mod:`tcg_api.datasets.fingerprints`, which imports neither, because #156's
splitter consumes stored hashes and must stay runnable outside this image.

**No fourth normalization path.** `detect` then `normalize`, in the order and
with the guards `tcg_api.analysis.quality._locate_judge_and_straighten` uses.
Spec §19's quality gate is deliberately *not* run here: a photograph the gate
would refuse is still a near duplicate of one it accepts, and skipping it would
hide exactly the leakage §32 forbids.

`main()` lives here, in the module whose work it runs, following
`tcg_api.datasets.ingestion` and `tcg_api.catalog.import_catalog`.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_ml_card_detection import CARD_DETECTION_VERSION
from tcg_ml_normalization import NORMALIZATION_VERSION
from tcg_shared.storage import StorageError, StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.fingerprints import (
    DHASH_VERSION,
    NEAR_DUPLICATE_DISTANCE,
    Fingerprint,
    difference_hash,
    distance,
    near_duplicate_groups,
    near_duplicate_pairs,
    read_fingerprints,
)
from tcg_api.datasets.normalization import artifact
from tcg_api.datasets.tables import training_image_fingerprints, training_images
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "HASH_VERSION",
    "FingerprintRun",
    "fingerprint_artifact",
    "fingerprint_pending",
    "main",
    "measure",
    "run",
]

logger = logging.getLogger(__name__)

#: What produced a stored hash — the hash, the detector and the normalizer, the
#: shape `tcg_api.analysis.quality.PIPELINE_VERSION` composes its three in. The
#: artifact is what gets hashed, so a detector or normalizer bump changes the
#: answer and every stored row is stale.
#:
#: `NEAR_DUPLICATE_DISTANCE` is deliberately **not** in it: the threshold is not
#: stored, so moving it invalidates nothing on disk. It changes the answer to a
#: question asked live.
HASH_VERSION: Final = "+".join((DHASH_VERSION, CARD_DETECTION_VERSION, NORMALIZATION_VERSION))


@dataclass(frozen=True, slots=True)
class FingerprintRun:
    """What one pass over the corpus did, for the operator report.

    Args:
        computed: Images fingerprinted this run.
        already_current: Images whose stored fingerprint was already this
            `HASH_VERSION`.
        unlocatable: Images examined that yielded no artifact — no card was
            found in them. Recorded as a row with neither hash, so they are not
            examined again until a version bumps.
        unreadable: Images whose stored object did not come back. Nothing is
            recorded for these, so the next run retries them.
    """

    computed: int
    already_current: int
    unlocatable: int
    unreadable: int


def fingerprint_artifact(data: bytes) -> tuple[str, str] | None:
    """Locate the card in a photograph, straighten it, and hash the result.

    Returns ``None`` when no card could be located or the warp could not be
    encoded, which is exactly what :func:`tcg_api.datasets.normalization.artifact`
    answers with — the two guards live there so this module and #159's pass
    cannot drift into two detect-then-straighten paths.
    """
    straightened = artifact(data)
    if straightened is None:
        return None
    return difference_hash(straightened.data)


async def fingerprint_pending(engine: AsyncEngine, storage: ObjectStorage) -> FingerprintRun:
    """Fingerprint every image that has no current fingerprint.

    Three phases, so no transaction is held open across the CV work: read what is
    pending and close the connection, compute, then write in one transaction.

    The compute step is called inline rather than through `anyio.to_thread`.
    `tcg_api.analysis.quality._prepare_one` hops off the event loop because other
    requests are waiting on it; here nothing else is running, so the hop would
    buy a thread and no concurrency.
    """
    async with engine.connect() as connection:
        pending = (
            await connection.execute(
                sa.select(training_images.c.id, training_images.c.original_uri)
                .select_from(
                    training_images.outerjoin(
                        training_image_fingerprints,
                        training_image_fingerprints.c.training_image_id == training_images.c.id,
                    )
                )
                .where(
                    sa.or_(
                        training_image_fingerprints.c.training_image_id.is_(None),
                        training_image_fingerprints.c.hash_version != HASH_VERSION,
                    )
                )
                .order_by(training_images.c.created_at)
            )
        ).all()
        already_current = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(training_image_fingerprints)
                .where(training_image_fingerprints.c.hash_version == HASH_VERSION)
            )
        ).scalar_one()

    computed: list[dict[str, object]] = []
    unlocatable = 0
    unreadable = 0
    for row in pending:
        try:
            data = await storage.get(StorageKey(row.original_uri))
        except StorageError:
            # ponytail: an object that never comes back is re-fetched on every
            # run, for ever. Recording the failure would be a state column for a
            # defect rather than for a fact — a corpus row naming bytes nobody
            # has is something to see and fix, not something to remember. If it
            # ever needs fixing, it is #41's retention sweep reconciling rows
            # against storage, not a column here.
            logger.warning("training image %s: its stored object could not be read", row.id)
            unreadable += 1
            continue

        hashes = fingerprint_artifact(data)
        if hashes is None:
            unlocatable += 1
        computed.append(
            {
                "training_image_id": row.id,
                "perceptual_hash": hashes[0] if hashes else None,
                "perceptual_hash_rotated": hashes[1] if hashes else None,
                "hash_version": HASH_VERSION,
            }
        )

    if computed:
        async with engine.begin() as connection:
            statement = insert(training_image_fingerprints)
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[training_image_fingerprints.c.training_image_id],
                    set_={
                        "perceptual_hash": statement.excluded.perceptual_hash,
                        "perceptual_hash_rotated": statement.excluded.perceptual_hash_rotated,
                        "hash_version": statement.excluded.hash_version,
                        "computed_at": sa.func.now(),
                    },
                ),
                computed,
            )

    return FingerprintRun(
        computed=len(computed),
        already_current=already_current,
        unlocatable=unlocatable,
        unreadable=unreadable,
    )


async def _copies(engine: AsyncEngine) -> dict[uuid.UUID, uuid.UUID | None]:
    """Which physical copy each training image belongs to, or `None` for class 4."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            sa.select(training_images.c.id, training_images.c.physical_copy_id)
        )
    return {row.id: row.physical_copy_id for row in rows}


def _report(
    fingerprints: tuple[Fingerprint, ...],
    copies: dict[uuid.UUID, uuid.UUID | None],
    *,
    threshold: int,
) -> None:
    """What an operator reads before annotating anything."""
    groups = near_duplicate_groups(fingerprints, threshold=threshold)
    closest = {
        frozenset((left, right)): apart
        for left, right, apart in near_duplicate_pairs(fingerprints, threshold=threshold)
    }
    linked = sum(len(group) for group in groups)

    logger.info(
        "exact duplicates are unrepresentable here: uq_training_images_sha256 refuses a "
        "second ingest of identical bytes, so this pass reports none"
    )
    logger.info(
        "%d of %d fingerprinted image(s) fall into %d near-duplicate group(s) "
        "at a Hamming distance of %d or less",
        linked,
        len(fingerprints),
        len(groups),
        threshold,
    )

    for number, group in enumerate(groups, start=1):
        members = sorted(group, key=str)
        within = {copies.get(member) for member in members}
        spanning = len(within) > 1 or None in within
        tightest = min(
            (apart for pair, apart in closest.items() if pair <= group),
            default=threshold,
        )
        logger.info(
            "  group %d: %d image(s), closest pair %d bit(s), %s — %s",
            number,
            len(members),
            tightest,
            "spans more than one physical copy" if spanning else "one physical copy, no news",
            ", ".join(
                f"{member} (copy {copies.get(member) or 'unidentified'})" for member in members
            ),
        )

    if any(len(group) > 1 for group in groups):
        logger.info(
            "a group spanning two copies is either one card entered twice under two "
            "submissions, or two copies of one printing the hash cannot tell apart. "
            "This pass cannot distinguish them; a person must"
        )
    logger.info(
        "the threshold is provisional: %d bits was chosen against synthetic fixtures and "
        "has not been measured against real photographs. Run --measure over a directory "
        "of real cards and read the valley off the histogram",
        threshold,
    )


def measure(directory: Path, *, threshold: int) -> None:
    """Fingerprint a local directory and report the distance distribution.

    Touches neither the database nor object storage, and stores nothing. This is
    the instrument that turns :data:`~tcg_api.datasets.fingerprints.NEAR_DUPLICATE_DISTANCE`
    from a judgement into a measurement: the operator points it at their own
    photographs, reads the valley between "same card" and "different card" off
    the histogram, and moves the constant to sit in it.

    Filenames appear in the output because the directory is the operator's own
    local corpus. Spec §54 governs the product's user data, not this.
    """
    named: list[tuple[str, Fingerprint]] = []
    unlocatable = 0
    paths = sorted(path for path in directory.iterdir() if path.is_file())
    for path in paths:
        hashes = fingerprint_artifact(path.read_bytes())
        if hashes is None:
            unlocatable += 1
            continue
        named.append(
            (
                path.name,
                Fingerprint(
                    # A synthetic identifier: nothing here is stored, and the
                    # name is what the operator reads the result by.
                    training_image_id=uuid.uuid4(),
                    perceptual_hash=hashes[0],
                    perceptual_hash_rotated=hashes[1],
                ),
            )
        )

    logger.info(
        "measure: %d file(s), %d fingerprinted, %d with no card located",
        len(paths),
        len(named),
        unlocatable,
    )
    if len(named) < 2:
        logger.info("measure: fewer than two fingerprints; there is nothing to compare")
        return

    apart: list[tuple[int, str, str]] = []
    for index, (left_name, left) in enumerate(named):
        for right_name, right in named[index + 1 :]:
            bits = distance(left, right)
            if bits is not None:
                apart.append((bits, left_name, right_name))
    apart.sort()

    histogram = collections.Counter(bits for bits, _, _ in apart)
    logger.info(
        "measure: %d pair(s); distance min %d, median %d, max %d",
        len(apart),
        apart[0][0],
        apart[len(apart) // 2][0],
        apart[-1][0],
    )
    logger.info(
        "measure: histogram (bits:pairs) %s",
        " ".join(f"{bits}:{count}" for bits, count in sorted(histogram.items())),
    )
    logger.info(
        "measure: %d pair(s) at or below %d bit(s):",
        sum(1 for bits, _, _ in apart if bits <= threshold),
        threshold,
    )
    for bits, left_name, right_name in apart:
        if bits > threshold:
            break
        logger.info("  %s <-> %s (%d bit(s))", left_name, right_name, bits)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--threshold",
        type=int,
        default=NEAR_DUPLICATE_DISTANCE,
        metavar="BITS",
        help=(
            "how many of the 64 bits may differ before two images are the same photograph "
            "(default: %(default)s). This changes the report only — nothing about the "
            "threshold is stored, and the pinned default is what the splitter uses."
        ),
    )
    parser.add_argument(
        "--measure",
        type=Path,
        default=None,
        metavar="DIRECTORY",
        help=(
            "fingerprint a local directory of photographs and report the distance "
            "distribution instead of running the corpus pass. Reads no database, writes "
            "nothing, ingests nothing. This is how the threshold stops being provisional."
        ),
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if arguments.threshold < 0 or arguments.threshold > 64:
        parser.error(
            "--threshold is a count of differing bits in a 64-bit hash, so it lies between 0 and 64"
        )
    if arguments.measure is not None and not arguments.measure.is_dir():
        parser.error(f"--measure needs a directory to read; {arguments.measure} is not one")


async def run(arguments: argparse.Namespace) -> FingerprintRun:
    """Fingerprint what is pending, then report the groups."""
    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    try:
        pass_result = await fingerprint_pending(engine, storage)
        copies = await _copies(engine)
        async with engine.connect() as connection:
            fingerprints = await read_fingerprints(connection)
    finally:
        await engine.dispose()

    logger.info(
        "training images fingerprinted: %d computed, %d already current, "
        "%d with no card located, %d whose object could not be read",
        pass_result.computed,
        pass_result.already_current,
        pass_result.unlocatable,
        pass_result.unreadable,
    )
    _report(fingerprints, copies, threshold=arguments.threshold)
    return pass_result


def main() -> int:
    """Console-script entry point (`uv run tcg-detect-duplicate-training-images`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    if arguments.measure is not None:
        measure(arguments.measure, threshold=arguments.threshold)
        return 0

    asyncio.run(run(arguments))
    # A near duplicate is a finding for #156 to consume, not a failure — this
    # returns 0 whether or not it found any.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
