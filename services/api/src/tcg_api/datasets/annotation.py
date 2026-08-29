"""What spec §30's annotation tool reads: a work list, one image, its bytes.

The reads behind `/internal/annotation`. `routers/annotation.py` holds HTTP and
nothing else, on `routers/cards.py`'s rule, and this is where the statements
live — which is also where #160's writes into `image_annotations` and
`centering_measurements` belong when it lands.

**Nothing here produces an artifact.** It resolves `training_images.normalized_uri`
and fetches the object under it. Straightening a photograph needs OpenCV, which
`tests/test_import_purity.py` keeps out of every module `tcg_api.main` can
reach, so an artifact exists here only because
:mod:`tcg_api.datasets.normalization` produced it out of band — and an image
that has none is answered honestly rather than warped on the spot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.database import execute
from tcg_api.datasets.tables import (
    centering_measurements,
    image_annotations,
    training_images,
)

__all__ = [
    "ARTIFACT_MEDIA_TYPE",
    "DatasetStoreUnavailable",
    "Representation",
    "StoredImage",
    "TrainingImageDetail",
    "TrainingImageSummary",
    "WorkList",
    "read_bytes",
    "read_image",
    "read_work_list",
]

logger = logging.getLogger(__name__)

_UNREACHABLE: Final = "the training image corpus could not be read"

#: What a stored artifact is, spelled out rather than imported.
#: `tcg_ml_normalization.MEDIA_TYPE` is the source of truth and importing it
#: here would put OpenCV on the request path, which is the one thing
#: `tests/test_import_purity.py` will not allow — `datasets/tables.py` keeps
#: 756 and 1056 out of the schema for the same reason. If the artifact ever
#: stops being a PNG, this and that constant move together.
ARTIFACT_MEDIA_TYPE: Final = "image/png"


class DatasetStoreUnavailable(ConnectionError):
    """The dataset store could not be reached.

    The fifth per-domain name `tcg_api.database.execute` anticipates, beside
    `CatalogUnavailable`, `AnalysisStoreUnavailable`, `GradingRulesUnavailable`
    and `MarketSnapshotUnavailable`. Which store was unreachable is what a route
    turns into a `details.reason`, which is why this is not shared.
    """


#: Which of an image's two representations a caller wants. `normalized` is the
#: standardized artifact #158's coordinates are fractions of; `original` is the
#: photograph, which is all there is when no card could be located.
#:
#: A `str` rather than a `StrEnum` in `tcg_domain`: it names two objects this
#: schema stores, not a concept the domain reasons about, and `ImageSide` is
#: what the domain already owns here.
Representation = str

NORMALIZED: Final[Representation] = "normalized"
ORIGINAL: Final[Representation] = "original"
REPRESENTATIONS: Final = (NORMALIZED, ORIGINAL)


@dataclass(frozen=True, slots=True)
class TrainingImageSummary:
    """One row of the work list, and one entry in an image's `siblings`."""

    id: UUID
    side: str
    card_id: UUID | None
    physical_copy_id: UUID | None
    source: str
    created_at: datetime
    has_artifact: bool


@dataclass(frozen=True, slots=True)
class TrainingImageDetail:
    """One image, with the other photographs of the same physical copy.

    Args:
        siblings: **Empty when `physical_copy_id` is NULL**, which is an honest
            answer rather than a gap: ADR 0008's approved class 4 identifies no
            copy, and treating NULL as a group would make every consented upload
            a sibling of every other one.
        representation: `normalized` when an artifact was stored, `original`
            otherwise. The server answers this so the tool never has to guess
            which space it is showing — and #160 takes coordinates only against
            `normalized`.
    """

    id: UUID
    side: str
    card_id: UUID | None
    physical_copy_id: UUID | None
    source: str
    created_at: datetime
    width: int
    height: int
    representation: Representation
    siblings: tuple[TrainingImageSummary, ...]


@dataclass(frozen=True, slots=True)
class StoredImage:
    """The bytes of one representation, and the type they were stored as."""

    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class WorkList:
    """One page of the images awaiting annotation, and how many there are."""

    images: tuple[TrainingImageSummary, ...]
    total: int


#: Awaiting annotation is the absence of **both** kinds of row. §30 splits its
#: eleven features across two tables (#158), so an image carrying only a
#: centering measurement has been worked on — a single anti-join against
#: `image_annotations` would put it back in the queue and invite a second,
#: contradictory reading.
#:
#: `NOT EXISTS` rather than two `LEFT JOIN … IS NULL`: both child tables carry an
#: index on `training_image_id`, which is exactly what an anti-join uses, and a
#: double outer join multiplies rows and then needs `DISTINCT` to undo itself.
_AWAITING_ANNOTATION: Final = sa.and_(
    sa.not_(sa.exists().where(image_annotations.c.training_image_id == training_images.c.id)),
    sa.not_(sa.exists().where(centering_measurements.c.training_image_id == training_images.c.id)),
)

_SUMMARY_COLUMNS: Final = (
    training_images.c.id,
    training_images.c.side,
    training_images.c.card_id,
    training_images.c.physical_copy_id,
    training_images.c.source,
    training_images.c.created_at,
    training_images.c.normalized_uri,
)


def _summary(row: sa.Row[tuple[object, ...]]) -> TrainingImageSummary:
    return TrainingImageSummary(
        id=row.id,
        side=row.side,
        card_id=row.card_id,
        physical_copy_id=row.physical_copy_id,
        source=row.source,
        created_at=row.created_at,
        # The key itself never leaves the service: it is server-generated and
        # internal (spec §55), and a badge only needs to know there is one.
        has_artifact=row.normalized_uri is not None,
    )


async def read_work_list(db: AsyncSession, *, limit: int, offset: int) -> WorkList:
    """Return one page of the images nobody has annotated yet.

    Ordered by `(created_at, id)` — a total order, so paging neither drops nor
    duplicates a row, which `GET /cards/search` established and for the same
    reason.

    ponytail: offset paging over a set that *shrinks* as annotations land, so a
    page boundary can skip a row once #160 writes. A keyset cursor on
    `(created_at, id)` is the fix, and it is worth building when an annotator
    trips over it rather than before — nothing writes an annotation today.
    """
    statement = (
        sa.select(*_SUMMARY_COLUMNS, sa.func.count().over().label("total"))
        .where(_AWAITING_ANNOTATION)
        .order_by(training_images.c.created_at, training_images.c.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).all()

    if not rows:
        # `count(*) OVER ()` travels on the rows, so an empty page carries no
        # total. Ask for it separately rather than reporting zero: an offset past
        # the end is an empty page of a non-empty queue.
        total_statement = (
            sa.select(sa.func.count()).select_from(training_images).where(_AWAITING_ANNOTATION)
        )
        total = (
            await execute(
                db, total_statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE
            )
        ).scalar_one()
        return WorkList(images=(), total=int(total))

    return WorkList(
        images=tuple(_summary(row) for row in rows),
        total=int(rows[0].total),
    )


async def read_image(db: AsyncSession, image_id: UUID) -> TrainingImageDetail | None:
    """Return one image and its siblings, or `None` if there is no such image."""
    statement = sa.select(
        *_SUMMARY_COLUMNS,
        training_images.c.width,
        training_images.c.height,
    ).where(training_images.c.id == image_id)
    row = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).one_or_none()
    if row is None:
        return None

    siblings: tuple[TrainingImageSummary, ...] = ()
    if row.physical_copy_id is not None:
        # A second statement rather than a self-join, because the NULL guard is
        # the thing that must not be got wrong here and a guard a reviewer can
        # see is worth a round trip. `ix_training_images_physical_copy_id` is
        # partial on exactly this predicate.
        sibling_statement = (
            sa.select(*_SUMMARY_COLUMNS)
            .where(
                training_images.c.physical_copy_id == row.physical_copy_id,
                training_images.c.id != image_id,
            )
            .order_by(training_images.c.side, training_images.c.id)
        )
        siblings = tuple(
            _summary(sibling)
            for sibling in (
                await execute(
                    db,
                    sibling_statement,
                    unavailable=DatasetStoreUnavailable,
                    message=_UNREACHABLE,
                )
            ).all()
        )

    return TrainingImageDetail(
        id=row.id,
        side=row.side,
        card_id=row.card_id,
        physical_copy_id=row.physical_copy_id,
        source=row.source,
        created_at=row.created_at,
        width=row.width,
        height=row.height,
        representation=NORMALIZED if row.normalized_uri is not None else ORIGINAL,
        siblings=siblings,
    )


async def read_bytes(
    db: AsyncSession,
    storage: ObjectStorage,
    image_id: UUID,
    *,
    representation: Representation,
) -> StoredImage | None:
    """Return one representation's bytes, or `None` if it does not exist.

    `None` covers both "no such image" and "that image has no artifact", and
    deliberately: the detail endpoint has already told the caller which
    representation exists, so asking for the other one is asking for something
    that is not there. Substituting the photograph instead would hand #160 a
    frame whose coordinates mean nothing, silently.

    Raises:
        DatasetStoreUnavailable: If the row could not be read.
        StorageError: If the object store could not be reached.
        ObjectNotFound: If the row names bytes the store does not hold. That is
            a disagreement between two stores rather than a missing thing, and
            the route answers it differently from a `None`.
    """
    statement = sa.select(
        training_images.c.original_uri,
        training_images.c.normalized_uri,
        training_images.c.mime_type,
    ).where(training_images.c.id == image_id)
    row = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).one_or_none()
    if row is None:
        return None

    if representation == NORMALIZED:
        if row.normalized_uri is None:
            return None
        key, media_type = row.normalized_uri, ARTIFACT_MEDIA_TYPE
    else:
        key, media_type = row.original_uri, row.mime_type

    return StoredImage(data=await storage.get(StorageKey(key)), media_type=media_type)
