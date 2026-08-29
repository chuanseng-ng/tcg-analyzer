"""Spec §28's normalization step, on the training corpus.

Stores the standardized artifact spec §30's annotation tool shows and #158's
coordinates are fractions of. `training_images.original_uri` names a
photograph; an annotator marking a corner at 12% across *that frame* has said
nothing comparable about the card, because the next photograph of it is framed
differently. The artifact is what makes a coordinate mean something, so it has
to be an object with a key of its own.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
    uv run tcg-normalize-training-images

**This runs from the worker image only**, on
:mod:`tcg_api.datasets.deduplication`'s terms and for its reason: straightening
a card needs `ml/card-detection` and `ml/normalization`, which is
`services/api`'s `worker` extra. The API image installs none of it and
`services/api/tests/test_import_purity.py` keeps that true — which is also why
the artifact cannot be produced while a request waits for one, and why this is a
pass rather than a route.

**This module owns the one detect-then-straighten path.** `deduplication`'s
docstring already forbade a fourth, so `fingerprint_artifact` hashes what
:func:`artifact` returns rather than repeating its two guards.

**A stored artifact is never replaced.** The pass selects rows whose
`normalized_uri` is NULL and nothing else. A `NORMALIZATION_VERSION` bump does
not make an artifact stale here the way it makes a fingerprint stale: #158
stores an annotation as a fraction of *the artifact the annotator saw*, so
re-warping an image somebody has already judged would move every stored
coordinate without touching a row in `image_annotations`. Re-normalizing a
corpus is a deliberate act with a re-annotation behind it, which is why there is
no `--force`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_domain.card_geometry import CardGeometry
from tcg_ml_card_detection import detect
from tcg_ml_normalization import MEDIA_TYPE, Normalized, normalize
from tcg_shared.storage import StorageError, StorageKey, generate_key
from tcg_shared.storage.port import ObjectStorage

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.tables import training_images
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "ARTIFACT_NAMESPACE",
    "NormalizationRun",
    "artifact",
    "main",
    "normalize_pending",
    "run",
]

logger = logging.getLogger(__name__)

#: The storage namespace training artifacts live under, deliberately distinct
#: from `tcg_api.analysis.quality.NORMALIZED_NAMESPACE`'s `normalized`. Spec
#: §54's retention sweep deletes an analysis's objects and a training image
#: outlives every analysis, so the two must never share a prefix.
#:
#: Spelled out rather than imported: `tcg_api.analysis.quality` runs
#: `from tcg_ml_image_quality import assess` at import time, and pulling §19's
#: gate into a pass that deliberately does not gate is a worse dependency than
#: a repeated string literal.
ARTIFACT_NAMESPACE: Final = "training-normalized"


@dataclass(frozen=True, slots=True)
class NormalizationRun:
    """What one pass over the corpus did, for the operator report.

    Args:
        stored: Images whose artifact was produced and stored this run.
        already_stored: Images that already had one. Never re-examined.
        unlocatable: Images examined that yielded no artifact — no card was
            found in them. Nothing is recorded, so they are examined again next
            run, and the annotation tool renders them from the photograph while
            saying so.
        unreadable: Images whose stored object did not come back. Nothing is
            recorded for these either, so the next run retries them.
    """

    stored: int
    already_stored: int
    unlocatable: int
    unreadable: int


def artifact(data: bytes) -> Normalized | None:
    """Locate the card in a photograph and straighten it.

    Returns ``None`` when no card could be located or the warp could not be
    encoded — both of which `ml/card-detection` and `ml/normalization` answer
    rather than raise, so this returns rather than raising too.

    The order and the guards are
    `tcg_api.analysis.quality._locate_judge_and_straighten`'s. Spec §19's
    quality gate is deliberately not run: a photograph the gate would refuse is
    still one an annotator may need to look at, and refusing to straighten it
    would leave the tool guessing why.
    """
    geometry = detect(data)
    if not isinstance(geometry, CardGeometry):
        return None
    straightened = normalize(data, geometry)
    if not isinstance(straightened, Normalized):
        return None
    return straightened


async def normalize_pending(engine: AsyncEngine, storage: ObjectStorage) -> NormalizationRun:
    """Store an artifact for every training image that has none.

    One transaction per image rather than deduplication's single batched write.
    The bytes are already in object storage by the time the row is updated, so
    an interrupted run over a large corpus keeps the work it finished instead of
    discarding all of it — and an artifact nothing references is cheaper to
    leave behind than a run is to repeat.

    The CV work runs inline rather than through `anyio.to_thread`, on
    `fingerprint_pending`'s reasoning: nothing else is running, so the hop would
    buy a thread and no concurrency.
    """
    async with engine.connect() as connection:
        pending = (
            await connection.execute(
                sa.select(training_images.c.id, training_images.c.original_uri)
                .where(training_images.c.normalized_uri.is_(None))
                .order_by(training_images.c.created_at)
            )
        ).all()
        already_stored = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(training_images)
                .where(training_images.c.normalized_uri.is_not(None))
            )
        ).scalar_one()

    stored = 0
    unlocatable = 0
    unreadable = 0
    for row in pending:
        try:
            data = await storage.get(StorageKey(row.original_uri))
        except StorageError:
            # ponytail: an object that never comes back is re-fetched on every
            # run, for ever — `tcg_api.datasets.deduplication` gives the reason
            # not to record the failure, and it holds identically here.
            logger.warning("training image %s: its stored object could not be read", row.id)
            unreadable += 1
            continue

        straightened = artifact(data)
        if straightened is None:
            # No card was located, so there is nothing to straighten. Nothing is
            # written: the row stays pending, and the annotation tool falls back
            # to the photograph and labels it rather than showing a frame nobody
            # can take a coordinate against.
            logger.info("training image %s: no card was located, so no artifact", row.id)
            unlocatable += 1
            continue

        key = generate_key(ARTIFACT_NAMESPACE)
        await storage.put(key, straightened.data, content_type=MEDIA_TYPE)
        details: dict[str, Any] = dict(straightened.as_record())
        async with engine.begin() as connection:
            await connection.execute(
                sa.update(training_images)
                .where(training_images.c.id == row.id)
                .values(normalized_uri=str(key), normalization_details=details)
            )
        stored += 1

    return NormalizationRun(
        stored=stored,
        already_stored=already_stored,
        unlocatable=unlocatable,
        unreadable=unreadable,
    )


async def run() -> NormalizationRun:
    """Store what is pending, then report."""
    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    try:
        result = await normalize_pending(engine, storage)
    finally:
        await engine.dispose()

    logger.info(
        "training image artifacts: %d stored, %d already stored, "
        "%d with no card located, %d whose object could not be read",
        result.stored,
        result.already_stored,
        result.unlocatable,
        result.unreadable,
    )
    return result


def main() -> int:
    """Console-script entry point (`uv run tcg-normalize-training-images`)."""
    argparse.ArgumentParser(description=__doc__, add_help=True).parse_args()

    configure_logging(get_settings())
    asyncio.run(run())
    # An image with no locatable card is a finding for an annotator, not a
    # failure of the pass — this returns 0 whether or not there were any.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
